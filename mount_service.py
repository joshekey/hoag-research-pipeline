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
from pathlib import Path
from store import audit, database, idle, load_config

BASE = Path('/etc/hoag-research')
FSTAB = Path('/etc/fstab')
SOCKET = '/run/hoag-mount/control.sock'
TARGETS = {'bulk': '/mnt/hoag-bulk', 'imaging': '/mnt/hoag-imaging', 'output': '/mnt/hoag-output'}


def validate(body):
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
    if body.get('confirm') is not True:
        raise ValueError('Confirm that this share may be connected or reconnected')
    return {**values, 'unc': unc, 'slot': body['slot']}


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
    expected = config['output_root'] if data['slot'] == 'output' else config['source_roots'][0 if data['slot'] == 'bulk' else 1]
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
        db.execute("UPDATE settings SET value='0' WHERE key='scan_complete'")
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
    return {'message': 'Share connected. Credentials saved locally; automatic mounting enabled. Rescan before review or export.'}


def request_mount(body):
    # Credentials travel only over the local protected Unix socket, never as process arguments.
    validate(body)
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
                    result = apply(json.loads(raw), load_config(BASE / 'config.json'))
                except Exception as error:
                    result = {'error': str(error) if type(error) is ValueError else 'Mount helper failed; check service status'}
                try:
                    connection.sendall(json.dumps(result).encode() + b'\n')
                except OSError:
                    pass


if __name__ == '__main__':
    main()
