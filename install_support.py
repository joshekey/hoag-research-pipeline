"""Standard-library update checks and restricted, consistent state backups."""
import argparse
from contextlib import closing
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path


def check_idle(config_path, reserve=False, release=False):
    config_path = Path(config_path)
    if not config_path.exists():
        return
    config = json.loads(config_path.read_text())
    path = Path(config['state_dir']) / 'workflow.sqlite'
    if not path.exists():
        return
    mode = 'rw' if reserve or release else 'ro'
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=' + mode, uri=True)) as db, db:
        if reserve or release:
            db.execute('BEGIN IMMEDIATE')
        if release:
            db.execute("DELETE FROM settings WHERE key='installation_active'")
            return
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError('An indexing or processing job is active. Wait for completion before updating.')
        if reserve:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('installation_active','1')")


def backup(config_dir, app_dir, system_dir, destination, legacy_state):
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(destination, 0o700)
    config_dir, app_dir, system_dir = map(Path, (config_dir, app_dir, system_dir))
    for source, name in [(config_dir, 'configuration'), (app_dir, 'application')]:
        if source.exists():
            shutil.copytree(source, destination / name, symlinks=True)
    units = destination / 'services'
    units.mkdir(mode=0o700)
    for name in ('hoag-dashboard.service', 'hoag-worker.service', 'hoag-mount.service'):
        source = system_dir / name
        if source.exists():
            shutil.copy2(source, units / name)
    # Use SQLite backup(), never copy a live database without its WAL.
    states = {Path(legacy_state).resolve()}
    if (config_dir / 'config.json').exists():
        states.add(Path(json.loads((config_dir / 'config.json').read_text())['state_dir']).resolve())
    entries = []
    for state in sorted(states):
        for name in ('workflow.sqlite', 'catalog.sqlite'):
            source = state / name
            if source.exists():
                target = destination / f'database-{len(entries)}.sqlite'
                with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as old:
                    with closing(sqlite3.connect(target)) as new:
                        old.backup(new)
                os.chmod(target, 0o600)
                entries.append({'source': str(source), 'backup': target.name})
    (destination / 'databases.json').write_text(json.dumps(entries, indent=2))
    return destination


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('check-idle', 'reserve', 'release', 'backup'))
    parser.add_argument('--destination')
    args = parser.parse_args()
    check_idle('/etc/hoag-research/config.json', reserve=args.action == 'reserve', release=args.action == 'release')
    if args.action == 'backup':
        result = backup('/etc/hoag-research', '/opt/hoag-research', '/etc/systemd/system',
                        args.destination or f'/var/backups/hoag-research/{time.time_ns()}', '/var/lib/hoag-research')
        nginx = Path('/etc/nginx/conf.d/hoag-research.conf')
        if nginx.exists():
            shutil.copy2(nginx, result / 'nginx.conf')
        print(f'Restricted pre-update backup: {result}')


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error))
