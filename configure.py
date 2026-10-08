"""Root-only share configuration wizard. Credentials never enter GitHub or the GUI."""
import argparse
import getpass
import grp
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE = Path('/etc/hoag-research')


def save(config):
    temp = BASE / 'config.json.new'
    temp.write_text(json.dumps(config, indent=2))
    os.chmod(temp, 0o640)
    os.chown(temp, 0, grp.getgrnam('hoag-indexer').gr_gid)
    temp.replace(BASE / 'config.json')


def value(prompt, default=''):
    result = input(prompt + (f' [{default}]' if default else '') + ': ').strip() or default
    if any(c in result for c in ('\n', '\r', '\x00')):
        raise ValueError('Invalid input')
    return result


def mount_share(label, mountpoint, writable=False):
    unc = value(f'{label} Windows UNC path (blank retains existing mount)')
    if not unc:
        return
    unc = unc.replace('\\', '/').rstrip('/')
    if not unc.startswith('//') or len(unc[2:].split('/')) != 2:
        raise ValueError('Enter a share root such as //SERVER/SHARE NAME')
    username = value('SMB username')
    domain = value('SMB domain (optional)')
    password = getpass.getpass('SMB password (hidden): ')
    if not username or not password or any(c in password for c in ('\n', '\r', '\x00')):
        raise ValueError('Username and a valid password are required')
    credential = BASE / (Path(mountpoint).name + '.credentials')
    credential.write_text(f'username={username}\npassword={password}\ndomain={domain}\n')
    os.chmod(credential, 0o600)
    mount = Path(mountpoint)
    mount.mkdir(parents=True, exist_ok=True)
    account = __import__('pwd').getpwnam('hoag-indexer')
    options = f'{"rw" if writable else "ro"},credentials={credential},vers=3.1.1,seal,uid={account.pw_uid},gid={account.pw_gid},file_mode={"0600" if writable else "0400"},dir_mode={"0700" if writable else "0500"},nosuid,nodev,noexec,_netdev,nofail,x-systemd.automount'
    fstab = Path('/etc/fstab')
    lines = fstab.read_text().splitlines()
    marker = '# hoag-research:' + mountpoint
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == mountpoint and not line.endswith(marker):
            raise ValueError('Mountpoint already exists in fstab; coordinate with IT')
    escaped = unc.replace(' ', '\\040').replace('\t', '\\011')
    entry = f'{escaped} {mountpoint} cifs {options} 0 0 {marker}'
    newtext = '\n'.join(line for line in lines if not line.endswith(marker)) + '\n' + entry + '\n'
    shutil.copy2(fstab, BASE / f'fstab-backup-{time.time_ns()}')
    fstab.write_text(newtext)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if os.path.ismount(mount):
        print(f'{mountpoint} is already mounted; saved settings require an IT-managed remount.')
    else:
        subprocess.run(['mount', mountpoint], check=True)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo')
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--reset-password', action='store_true')
    args = parser.parse_args()
    path = BASE / 'config.json'
    if args.initialize:
        if path.exists():
            return
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        password = secrets.token_urlsafe(24)
        config['password_hash'] = generate_password_hash(password, method='pbkdf2:sha256:1000000')
        config['secret_key'] = secrets.token_hex(32)
        save(config)
        (BASE / 'initial-admin-password').write_text(password + '\n')
        print('Configuration initialized; root-only initial password saved.')
        return
    config = json.loads(path.read_text())
    if args.reset_password:
        password = getpass.getpass('New administrator password (at least 16 characters): ')
        if len(password) < 16 or getpass.getpass('Repeat password: ') != password:
            raise ValueError('Password too short or confirmation differs')
        config['password_hash'] = generate_password_hash(password, method='pbkdf2:sha256:1000000')
        save(config)
        subprocess.run(['systemctl', 'restart', 'hoag-dashboard'], check=True)
        return
    print('Configure two read-only sources and a separate writable output share.')
    print('Leave UNC blank to retain mounts already managed by hospital IT.')
    for label, mount in [('Bulk source', '/mnt/hoag-bulk'), ('Imaging source', '/mnt/hoag-imaging'), ('Research output', '/mnt/hoag-output')]:
        mount_share(label, mount, writable=label == 'Research output')
    config['report_encoding'] = value('TXT encoding (utf-8-sig, cp1252, utf-16)', config['report_encoding'])
    if config['report_encoding'] not in ('utf-8-sig', 'cp1252', 'utf-16'):
        raise ValueError('Unsupported report encoding')
    save(config)
    print('Configuration saved. Restart hoag-dashboard and hoag-worker if already running.')


if __name__ == '__main__':
    main()
