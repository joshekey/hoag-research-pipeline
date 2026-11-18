"""Root-owned, fixed-purpose mount broker. No shell or caller-chosen mount options."""
import contextlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import time
import tempfile
from pathlib import Path
from store import audit, database, idle, load_config

BASE = Path('/etc/hoag-research')
FSTAB = Path('/etc/fstab')
SOCKET = '/run/hoag-mount/control.sock'
TARGETS = {'bulk': '/mnt/hoag-bulk', 'imaging': '/mnt/hoag-imaging', 'output': '/mnt/hoag-output', 'database': '/mnt/hoag-database'}


def validate(body):
    if isinstance(body, dict) and body.get('action') == 'inspect':
        return {'action': 'inspect'}
    if isinstance(body, dict) and body.get('action', 'apply') not in ('apply', 'test'):
        raise ValueError('Unknown mount action')
    if not isinstance(body, dict) or body.get('slot') not in TARGETS:
        raise ValueError('Choose a configured share slot')
    values = {}
    for key in ('unc', 'username', 'domain', 'password'):
        value = body.get(key, '')
        if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Invalid share or credential input')
        values[key] = value
    unc = values['unc'].replace(chr(92), '/').rstrip('/')
    if not re.fullmatch(r'//[A-Za-z0-9._-]+/[^/]+', unc) or '#' in unc:
        raise ValueError('Enter a share root such as //SERVER/SHARE NAME')
    if not values['username'] or not values['password']:
        raise ValueError('Username and password are required')
    if body.get('action', 'apply') == 'apply' and body.get('confirm') is not True:
        raise ValueError('Confirm that this share may be connected or reconnected')
    return {**values, 'unc': unc, 'slot': body['slot']}


def inspect_mounts(config):
    """Never send passwords or mount options over the socket."""
    lines = FSTAB.read_text().splitlines()
    result = []
    for slot, target in TARGETS.items():
        entries = [line for line in lines if not line.lstrip().startswith('#')
                   and len(line.split()) >= 2 and line.split()[1] == target]
        managed = not entries or (len(entries) == 1 and entries[0].endswith('# hoag-research:' + target))
        configured = target == config['output_root'] if slot == 'output' else target in config['source_roots'] or slot == 'database'
        item = dict(slot=slot, path=target, unc='', username='', domain='', editable=managed and configured)
        if entries:
            item['unc'] = entries[0].split()[0].replace(chr(92) + '040', ' ')
        credential = BASE / (Path(target).name + '.credentials')
        if managed and credential.is_file() and not credential.is_symlink():
            for line in credential.read_text().splitlines():
                key, _, value = line.partition('=')
                if key in ('username', 'domain'):
                    item[key] = value
        result.append(item)
    return {'mounts': result}


def test_connection(body, config):
    """Mount a candidate read-only in isolation; never reconnect the live share."""
    import pwd
    data = validate(body)
    account = pwd.getpwnam('hoag-indexer')
    # Reserve the worker queue, but do not invalidate scan/approval state for a test.
    with catalog(config, account) as db:
        db.execute('BEGIN IMMEDIATE')
        idle(db)
        with as_root():
            temp = Path(tempfile.mkdtemp(prefix='hoag-share-test-'))
            target = temp / 'share'
            credential = temp / 'credentials'
            mounted = False
            try:
                target.mkdir()
                # Permit the service user to reach the temporary mount, not its credentials.
                os.chmod(temp, 0o711)
                credential.write_text('username=' + data['username'] + '\npassword=' + data['password'] + '\ndomain=' + data['domain'] + '\n')
                os.chmod(credential, 0o600)
                options = f'ro,credentials={credential},vers=3.1.1,seal,uid={account.pw_uid},gid={account.pw_gid},file_mode=0400,dir_mode=0500,nosuid,nodev,noexec'
                command(['/usr/bin/mount', '-t', 'cifs', data['unc'], str(target), '-o', options])
                mounted = True
                previous = os.geteuid() if hasattr(os, 'geteuid') else None
                try:
                    if previous == 0:
                        os.seteuid(account.pw_uid)
                    names = []
                    with os.scandir(target) as children:
                        for index, child in enumerate(children):
                            if index >= 2000:
                                break
                            if child.is_dir(follow_symlinks=False):
                                names.append(child.name)
                                if len(names) == 50:
                                    break
                finally:
                    if previous == 0:
                        os.seteuid(previous)
                return {'message': 'Read-only connection test passed. Live mounts and catalog are unchanged. Output write access is checked after saving.',
                        'folders': sorted(names), 'limit': 50}
            except Exception:
                raise ValueError('Connection test failed. Check the share path, credentials, SMB access and folder permissions. Live mount settings were not changed.') from None
            finally:
                # Never recursively delete a directory that may still contain a mounted share.
                try:
                    if mounted or os.path.ismount(target):
                        command(['/usr/bin/umount', str(target)])
                finally:
                    credential.unlink(missing_ok=True)
                    if not os.path.ismount(target):
                        target.rmdir()
                        temp.rmdir()


def dispatch(body, config):
    validate(body)
    if body.get('action') == 'inspect':
        return inspect_mounts(config)
    if body.get('action') == 'test':
        return test_connection(body, config)
    return apply(body, config)


def command(args):
    subprocess.run(args, check=True, timeout=35, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@contextlib.contextmanager
def catalog(config, account):
    # Create SQLite WAL/SHM files as the service user, never as root.
    privileged = hasattr(os, 'geteuid') and os.geteuid() == 0
    if privileged:
        os.seteuid(account.pw_uid)
    try:
        with database(config) as db:
            yield db
    finally:
        if privileged:
            os.seteuid(0)


@contextlib.contextmanager
def as_root():
    previous = os.geteuid() if hasattr(os, 'geteuid') else None
    if previous is not None and os.getuid() == 0:
        os.seteuid(0)
    try:
        yield
    finally:
        if previous is not None and os.getuid() == 0:
            os.seteuid(previous)


def apply(body, config):
    import pwd
    data = validate(body)
    target = TARGETS[data['slot']]
    expected = target if data['slot']=='database' else config['output_root'] if data['slot'] == 'output' else config['source_roots'][0 if data['slot'] == 'bulk' else 1]
    if expected != target or Path(target).resolve() != Path(target):
        raise ValueError('GUI mounting requires the standard mountpoint; ask IT to configure custom paths')
    marker = '# hoag-research:' + target
    original = FSTAB.read_text()
    lines = original.splitlines()
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == target and not line.endswith(marker):
            raise ValueError('This mount is managed by IT; use the console or contact IT')
        if len(fields) >= 2 and fields[1] in TARGETS.values() and fields[1] != target:
            other = fields[0].replace(chr(92) + '040', ' ')
            if other.casefold() == data['unc'].casefold():
                raise ValueError('Use a separate share for each slot; output must be separate from sources')
    account = pwd.getpwnam('hoag-indexer')
    credential = BASE / (Path(target).name + '.credentials')
    previous = credential.read_bytes() if credential.exists() else None
    was_mounted = os.path.ismount(target)
    writable = data['slot'] == 'output'
    options = ','.join(['rw' if writable else 'ro', 'credentials=' + str(credential), 'vers=3.1.1', 'seal',
                        'uid=' + str(account.pw_uid), 'gid=' + str(account.pw_gid),
                        'file_mode=' + ('0600' if writable else '0400'), 'dir_mode=' + ('0700' if writable else '0500'),
                        'nosuid', 'nodev', 'noexec', '_netdev', 'nofail', 'x-systemd.automount'])
    entry = data['unc'].replace(' ', chr(92) + '040') + ' ' + target + ' cifs ' + options + ' 0 0 ' + marker
    # Hold a catalog write reservation: queueing/reviews cannot race a remount.
    with catalog(config, account) as db:
        db.execute('BEGIN IMMEDIATE')
        idle(db)
        if not writable:
            db.execute("UPDATE settings SET value='0' WHERE key='scan_complete'")
            db.execute("INSERT OR REPLACE INTO settings VALUES ('suggestions_ready','0')")
            db.execute("UPDATE studies SET state='needs_review',approved_hash=NULL,approved_fingerprint=NULL")
        # Commit invalidation even when a connection fails or rollback remount is unavailable.
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        idle(db)
        try:
            with as_root():
                Path(target).mkdir(parents=True, exist_ok=True)
                if was_mounted:
                    command(['/usr/bin/umount', target])
                credential.write_text('username=' + data['username'] + '\npassword=' + data['password'] + '\ndomain=' + data['domain'] + '\n')
                os.chmod(credential, 0o600)
                shutil.copy2(FSTAB, BASE / ('fstab-backup-' + str(time.time_ns())))
                FSTAB.write_text('\n'.join(line for line in lines if not line.endswith(marker)) + '\n' + entry + '\n')
                command(['/usr/bin/systemctl', 'daemon-reload'])
                command(['/usr/bin/mount', target])
                if data['slot']=='database' and target not in config['source_roots']:
                    from configure import save
                    config=dict(config,source_roots=[*config['source_roots'],target])
                    save(config)
            audit(db, 'admin', 'mount-connected', data['slot'])
        except Exception:
            with as_root():
                FSTAB.write_text(original)
                if previous is not None:
                    credential.write_bytes(previous)
                    os.chmod(credential, 0o600)
                elif credential.exists():
                    credential.unlink()
                try:
                    command(['/usr/bin/systemctl', 'daemon-reload'])
                    if was_mounted and not os.path.ismount(target):
                        command(['/usr/bin/mount', target])
                except Exception:
                    pass
            raise ValueError('Connection failed. Prior settings restored; check credentials, SMB access and server mount status. A successful rescan is required.') from None
    return {'message': 'Share connected. Credentials saved locally; automatic mounting enabled. ' +
            ('Existing export packages stay on their original share. Check output health before exporting.' if writable else
             'Rescan before review or export. Choose all source folders you want to keep in the catalog.')}


def request_mount(body):
    # Credentials travel only over the local protected Unix socket, never as process arguments.
    validate(body)
    if not hasattr(socket, 'AF_UNIX'):
        raise ValueError('Mount helper requires the Linux server; connection editing is unavailable on this host')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(115)
            client.connect(SOCKET)
            client.sendall(json.dumps(body).encode() + b'\n')
            result = b''
            while not result.endswith(b'\n') and len(result) < 65536:
                block = client.recv(4096)
                if not block:
                    break
                result += block
        data = json.loads(result)
    except (OSError, ValueError):
        raise ValueError('Mount helper unavailable or timed out; check hoag-mount service and source status before retrying') from None
    if 'error' in data:
        raise ValueError(data['error'])
    return data


def main():
    import pwd
    os.umask(0o077)
    account = pwd.getpwnam('hoag-indexer')
    path = Path(SOCKET)
    path.parent.mkdir(mode=0o750, exist_ok=True)
    os.chown(path.parent, 0, account.pw_gid)
    if path.exists():
        path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(SOCKET)
        os.chown(SOCKET, 0, account.pw_gid)
        os.chmod(SOCKET, 0o660)
        server.listen(4)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(10)
                _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != account.pw_uid:
                    continue
                try:
                    raw = b''
                    while not raw.endswith(b'\n') and len(raw) <= 16384:
                        block = connection.recv(4096)
                        if not block:
                            break
                        raw += block
                    if len(raw) > 16384:
                        raise ValueError('Request too large')
                    result = dispatch(json.loads(raw), load_config(BASE / 'config.json'))
                except Exception as error:
                    result = {'error': str(error) if type(error) is ValueError else 'Mount helper failed; check service status'}
                try:
                    connection.sendall(json.dumps(result).encode() + b'\n')
                except OSError:
                    pass


if __name__ == '__main__':
    main()
