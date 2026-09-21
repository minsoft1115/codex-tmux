#!/usr/bin/env bash
# Install a shell-independent command and migrate the old managed Bash alias.
set -euo pipefail

prefix=${HOME:?HOME is not set}/.local
bashrc=$HOME/.bashrc
while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix|--bashrc)
            [[ $# -ge 2 && -n $2 ]] || { printf 'Missing value: %s\n' "$1" >&2; exit 1; }
            if [[ $1 == --prefix ]]; then prefix=$2; else bashrc=$2; fi
            shift 2 ;;
        --help|-h)
            printf 'Usage: %s [--prefix PATH] [--bashrc PATH]\nDefault prefix: ~/.local; Bash migration: ~/.bashrc\n' "$0"
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
    esac
done
command -v python3 >/dev/null || { printf 'Python 3.11+ is required.\n' >&2; exit 1; }
installer_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
python3 - "$installer_dir" "$prefix" "$bashrc" <<'PY'
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
files = {name: (project / name).read_bytes() for name in ('codex_tmux.py', 'usage.py')}
marker = '# Managed by codex-tmux/install.sh'
wrapper = ('#!/bin/sh\n' + marker + '\nexec python3 '
           + shlex.quote(str(app / 'codex_tmux.py')) + ' "$@"\n').encode()
if command.is_symlink() or (command.exists() and marker.encode() not in command.read_bytes().splitlines()):
    sys.exit(f'Refusing to overwrite an unmanaged command: {command}')

# Only remove the exact block owned by the previous installer.
original = rc.read_bytes() if rc.exists() else b''
lines = original.splitlines(keepends=True)
starts = [i for i, line in enumerate(lines) if line.rstrip(b'\r\n') == b'# >>> codex-tmux >>>']
ends = [i for i, line in enumerate(lines) if line.rstrip(b'\r\n') == b'# <<< codex-tmux <<<']
updated = original
if starts or ends:
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        sys.exit(f'Malformed codex-tmux block in {rc}; no changes made.')
    updated = b''.join(lines[:starts[0]] + lines[ends[0] + 1:])


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


if updated != original:
    # Validate before installing or changing the user's shell configuration.
    subprocess.run(['bash', '-n'], input=updated, check=True)
for name, content in files.items():
    write_atomic(app / name, content, 0o644)
write_atomic(command, wrapper, 0o755)
if updated != original:
    with tempfile.NamedTemporaryFile(dir=rc.parent, prefix=rc.name + '.codex-tmux-', suffix='.bak', delete=False) as backup:
        backup.write(original)
    write_atomic(rc, updated, rc.stat().st_mode & 0o777, check_bash=True)
    print(f'Removed old managed Bash alias. Backup: {backup.name}')
    print('In an existing Bash session, run: unalias codex-tmux 2>/dev/null; hash -r')
print(f'Installed: {command}')
print(f'Application: {app}')
if str(command.parent) not in os.environ.get('PATH', '').split(os.pathsep):
    print('Add this directory to your shell PATH: ' + str(command.parent))
    print('For Bash/Zsh: export PATH=' + shlex.quote(str(command.parent)) + ':"$PATH"')
for name in ('tmux', 'codex'):
    if not shutil.which(name):
        print(f'Note: {name} is not on PATH; it is needed when launching Codex.')
PY
