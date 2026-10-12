"""Administrator-guided import of local TXT/SQL into protected, service-readable sources."""
import os
import shutil
import subprocess
import time
from pathlib import Path

BASE=Path('/var/lib/hoag-research/local-input')


def inputs(txt,sql):
    if txt:
        root=Path(txt)
        if not root.is_absolute():raise ValueError('Enter an absolute TXT folder path')
        if root.is_symlink() or not root.is_dir():raise ValueError('TXT input must be a directory, not a symlink')
        for folder,dirs,names in os.walk(root,followlinks=False):
            if any((Path(folder)/d).is_symlink() for d in dirs):raise ValueError('Remove symlinks from the selected TXT folder')
            for name in names:
                source=Path(folder)/name
                if source.suffix.lower()!='.txt':continue
                if source.is_symlink():raise ValueError('TXT symlinks cannot be imported')
                yield 'txt',source,source.relative_to(root)
    if sql:
        source=Path(sql)
        if not source.is_absolute():raise ValueError('Enter an absolute SQL file path')
        if source.is_symlink() or not source.is_file() or source.suffix.lower()!='.sql':raise ValueError('SQL input must be an existing .sql file, not a symlink')
        yield 'sql',source,Path('database.sql')


def main():
    import fcntl
    import pwd
    from configure import save
    from mount_service import catalog,as_root
    from store import load_config,idle,audit
    if os.geteuid()!=0:raise ValueError('Run this local-source wizard with sudo')
    lock=Path('/run/hoag-research-install.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    config=load_config('/etc/hoag-research/config.json');account=pwd.getpwnam('hoag-indexer')
    with catalog(config,account) as db:idle(db)
    print('Import local TXT/SQL sources into protected storage. Originals remain unchanged.')
    print('Files stay on this hospital server. Blank skips a source. The DICOM mount stays configured.')
    txt=input('Absolute local TXT folder: ').strip();sql=input('Absolute local SQL file: ').strip()
    if not txt and not sql:return
    total=sum(source.stat().st_size for _,source,_ in inputs(txt,sql))
    if not total:raise ValueError('No nonempty TXT/SQL files found')
    if BASE.is_symlink():raise ValueError('Local input storage must not be a symlink')
    BASE.mkdir(parents=True,exist_ok=True);os.chown(BASE,0,account.pw_gid);os.chmod(BASE,0o750)
    if shutil.disk_usage(BASE).free < total+config['minimum_free_bytes']:raise ValueError('Insufficient local space for protected input copies')
    print('Import size: '+str(round(total/1024**3,2))+' GiB. Originals will be retained.')
    if input('Type IMPORT to copy and register these local sources: ').strip()!='IMPORT':return
    version=BASE/str(time.time_ns());version.mkdir(mode=0o750)
    count=0
    for role,source,relative in inputs(txt,sql):
        target=version/role/relative;target.parent.mkdir(parents=True,exist_ok=True)
        before=source.stat()
        subprocess.run(['/usr/bin/cp','--reflink=auto','--',str(source),str(target)],check=True)
        after=source.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Local source changed during import; registration stopped')
        os.chown(target,0,account.pw_gid);os.chmod(target,0o440);count+=1
        if count%1000==0:print(str(count)+' files copied')
    for folder,dirs,_ in os.walk(version):os.chown(folder,0,account.pw_gid);os.chmod(folder,0o550)
    subprocess.run(['/usr/sbin/restorecon','-RF',str(BASE)],check=True)
    new=[str(version/role) for role,value in [('txt',txt),('sql',sql)] if value]
    with catalog(config,account) as db:
        db.execute('BEGIN IMMEDIATE');idle(db)
        old=config.get('local_source_roots',[])
        config['source_roots']=[r for r in config['source_roots'] if r not in old]+new
        config['local_source_roots']=new
        with as_root():
            backup=Path('/etc/hoag-research')/('config-before-local-'+str(time.time_ns())+'.json')
            shutil.copy2('/etc/hoag-research/config.json',backup);os.chmod(backup,0o600)
            save(config)
        db.execute("UPDATE settings SET value='0' WHERE key='scan_complete'")
        db.execute("INSERT OR REPLACE INTO settings VALUES ('suggestions_ready','0')")
        db.execute("INSERT OR REPLACE INTO settings VALUES ('scan_folders','null')")
        db.execute("UPDATE studies SET state='needs_review',approved_hash=NULL,approved_fingerprint=NULL")
        audit(db,'local-console','register-local-report-sources')
    subprocess.run(['systemctl','restart','hoag-dashboard','hoag-worker','hoag-sql'],check=True)
    print(str(count)+' files imported. In Sources, select your DICOM mount and local TXT/SQL folders, then Index selected folders.')
    print('Previous protected imports remain on disk; remove them only after checking retention requirements with your administrator.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if type(error) is ValueError else 'Local import failed; configuration was not fully updated. Check input access, storage, and service status.')
        raise SystemExit(1)
