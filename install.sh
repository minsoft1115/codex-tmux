#!/usr/bin/env bash
# Install the command and a managed Bash alias for codex.
set -euo pipefail

prefix=${HOME:?HOME is not set}/.local
bashrc=$HOME/.bashrc
uninstall=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall)
            uninstall=1
            shift ;;
        --prefix|--bashrc)
            [[ $# -ge 2 && -n $2 ]] || { printf 'Missing value: %s\n' "$1" >&2; exit 1; }
            if [[ $1 == --prefix ]]; then prefix=$2; else bashrc=$2; fi
            shift 2 ;;
        --help|-h)
            printf 'Usage: %s [--prefix PATH] [--bashrc PATH] [--uninstall]\nDefault prefix: ~/.local; Bash alias: ~/.bashrc\n' "$0"
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
    esac
done
command -v python3 >/dev/null || { printf 'Python 3.11+ is required.\n' >&2; exit 1; }
installer_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
python3 - "$installer_dir" "$prefix" "$bashrc" "$uninstall" <<'PY'
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

if sys.version_info < (3, 11):
    sys.exit('Python 3.11+ is required.')
project = Path(sys.argv[1])
prefix = Path(sys.argv[2]).expanduser().resolve()
rc = Path(sys.argv[3]).expanduser().resolve()
app = prefix / 'share' / 'codex-tmux'
command = prefix / 'bin' / 'codex-tmux'
marker = '# Managed by codex-tmux/install.sh'


def write_atomic(path, content, mode, check_bash=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.codex-tmux-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        if check_bash:
            subprocess.run(['bash', '-n', str(temporary)], check=True)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)



def without_managed_alias(content):
    lines = content.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip(b'\r\n') == b'# >>> codex-tmux >>>']
    ends = [i for i, line in enumerate(lines) if line.rstrip(b'\r\n') == b'# <<< codex-tmux <<<']
    if not starts and not ends:
        return content
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        sys.exit(f'Malformed codex-tmux block in {rc}; no changes made.')
    return b''.join(lines[:starts[0]] + lines[ends[0] + 1:])


def update_bashrc(content):
    if content == original:
        return
    rc.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=rc.parent, prefix=rc.name + '.codex-tmux-', suffix='.bak', delete=False) as backup:
        backup.write(original)
    write_atomic(rc, content, rc.stat().st_mode & 0o777 if rc.exists() else 0o644, check_bash=True)
    print(f'Updated Bash alias. Backup: {backup.name}')
    print('For the current Bash session: source ' + shlex.quote(str(rc)))


original = rc.read_bytes() if rc.exists() else b''
base_rc = without_managed_alias(original)
if sys.argv[4] == '1':
    if command.is_symlink() or app.is_symlink():
        sys.exit('Refusing to uninstall through a symlink.')
    if not command.exists():
        if app.exists():
            sys.exit(f'Cannot verify installation ownership: {command} is missing; no changes made.')
        print('codex-tmux is already uninstalled.')
        sys.exit(0)
    content = command.read_bytes()
    if (marker.encode() not in content.splitlines()
            or ('exec python3 ' + shlex.quote(str(app / 'codex_tmux.py')) + ' "$@"').encode() not in content.splitlines()):
        sys.exit(f'Refusing to remove an unmanaged command: {command}')
    subprocess.run(['bash', '-n'], input=base_rc, check=True)
    targets = [app / name for name in ('codex_tmux.py', 'usage.py', 'install.sh')]
    cache = app / '__pycache__'
    if cache.is_dir() and not cache.is_symlink():
        for name in ('codex_tmux', 'usage'):
            targets.extend(cache.glob(name + '.*.pyc'))
    for path in targets:
        if path.is_dir() and not path.is_symlink():
            sys.exit(f'Refusing to remove a directory in place of an installed file: {path}')
    for path in targets:
        path.unlink(missing_ok=True)
    update_bashrc(base_rc)
    command.unlink()
    for directory in (cache, app):
        if directory.is_dir() and not directory.is_symlink():
            try:
                directory.rmdir()
            except OSError:
                print(f'Kept directory containing other files: {directory}')
    print(f'Uninstalled: {command}')
    sys.exit(0)

files = {name: (project / name).read_bytes() for name in ('codex_tmux.py', 'usage.py', 'install.sh')}
wrapper = ('#!/bin/sh\n' + marker + '\n'
           'if [ "$#" -eq 1 ] && [ "$1" = --uninstall ]; then\n'
           '    exec bash ' + shlex.quote(str(app / 'install.sh'))
           + ' --uninstall --prefix ' + shlex.quote(str(prefix))
           + ' --bashrc ' + shlex.quote(str(rc)) + '\nfi\n'
           'exec python3 ' + shlex.quote(str(app / 'codex_tmux.py')) + ' "$@"\n').encode()
if command.is_symlink() or (command.exists() and marker.encode() not in command.read_bytes().splitlines()):
    sys.exit(f'Refusing to overwrite an unmanaged command: {command}')

# A marker block is replaced on reinstall; unrelated aliases remain intact.
alias_command = shlex.join(['command', str(command)])
updated = (base_rc + (b'\n' if base_rc and not base_rc.endswith(b'\n') else b'')
           + ('# >>> codex-tmux >>>\n'
              + 'alias codex=' + shlex.quote(alias_command) + '\n'
              + '# <<< codex-tmux <<<\n').encode())


if updated != original:
    # Validate before installing or changing the user's shell configuration.
    subprocess.run(['bash', '-n'], input=updated, check=True)
for name, content in files.items():
    write_atomic(app / name, content, 0o644)
write_atomic(command, wrapper, 0o755)
update_bashrc(updated)
print(f'Installed: {command}')
print(f'Application: {app}')
if str(command.parent) not in os.environ.get('PATH', '').split(os.pathsep):
    print('Add this directory to your shell PATH: ' + str(command.parent))
    print('For Bash/Zsh: export PATH=' + shlex.quote(str(command.parent)) + ':"$PATH"')
for name in ('tmux', 'codex'):
    if not shutil.which(name):
        print(f'Note: {name} is not on PATH; it is needed when launching Codex.')
PY
