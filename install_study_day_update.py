"""Install report study-day conversion into the deployed Hoag v5 engine.

Run as root: /opt/hoag-research/venv/bin/python install_study_day_update.py
Creates a private backup. Does not regenerate reports or overwrite exports.
"""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import hoag_study_day as update


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run this installer with sudo.')
    app = Path('/opt/hoag-research')
    engine = app / 'engine.py'
    module = app / 'hoag_study_day.py'
    if engine.is_symlink() or module.is_symlink():
        raise SystemExit('Symbolic-link installation paths are not supported.')
    source = engine.read_text()
    expected = "REPORT_REDACTION_POLICY = 'patient-identifiers-v5-protect-dates-from-phone-detector'"
    if source.count(expected) != 1:
        raise SystemExit('Expected the deployed v5 engine; nothing installed.')
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'sanitize_report']
    if len(functions) != 1:
        raise SystemExit('Unexpected sanitizer definition.')
    fn = functions[0]
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id == 'text']
    if len(returns) != 1:
        raise SystemExit('Unexpected sanitizer return; nothing installed.')
    lines = source.splitlines(keepends=True)
    pos = returns[0].lineno - 1
    if lines[pos].strip() != 'return text':
        raise SystemExit('Unexpected return formatting.')
    indent = lines[pos][:len(lines[pos]) - len(lines[pos].lstrip())]
    lines[pos] = indent + 'return shift_date_of_service(text)\n'
    changed = ''.join(lines).replace(expected, 'REPORT_REDACTION_POLICY = ' + repr(update.POLICY))
    changed = 'from hoag_study_day import shift_date_of_service\n' + changed
    ast.parse(changed)
    backup = Path(tempfile.mkdtemp(prefix='hoag-before-study-day-', dir='/var/tmp'))
    os.chmod(backup, 0o700)
    shutil.copy2(engine, backup / 'engine.py')
    had_module = module.exists()
    if had_module:
        shutil.copy2(module, backup / module.name)
    services = ['hoag-worker.service', 'hoag-dashboard.service']
    active = [s for s in services if subprocess.run(['systemctl', 'is-active', '--quiet', s]).returncode == 0]
    def write(path, data):
        fd, temporary = tempfile.mkstemp(prefix='.study-day-', dir=app)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if active:
        subprocess.run(['systemctl', 'stop', *active], check=True)
    try:
        write(module, Path(update.__file__).read_bytes())
        write(engine, changed.encode('utf-8'))
        subprocess.run(['restorecon', str(engine), str(module)], check=True)
        subprocess.run([str(app / 'venv/bin/python'), '-c',
                        'import engine; assert engine.sanitize_report'], cwd=app, check=True)
        if active:
            subprocess.run(['systemctl', 'start', *active], check=True)
            subprocess.run(['systemctl', 'is-active', '--quiet', *active], check=True)
    except Exception:
        write(engine, (backup / 'engine.py').read_bytes())
        if had_module:
            write(module, (backup / module.name).read_bytes())
        elif module.exists():
            module.unlink()
        subprocess.run(['restorecon', str(engine)])
        if active:
            subprocess.run(['systemctl', 'start', *active])
        print('Installation failed; restored original scripts. Backup:', backup)
        raise
    print('Installed policy:', update.POLICY)
    print('Backup:', backup)
    print('Fresh --prepare-latest and --process-latest runs are required.')


if __name__ == '__main__':
    main()
