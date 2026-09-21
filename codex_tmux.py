#!/usr/bin/env python3
"""Launch Codex with a session-specific native tmux status bar."""
import argparse
import fcntl
import json
import os
import traceback
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from usage import LogReader, render_status, display_options

SELF = Path(__file__).resolve()
SOCKET = None


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        json.dump(value, stream)
        temporary = Path(stream.name)
    temporary.replace(path)


def root_hook(state):
    """Only shell wrappers may sit between our hook and the launched CLI (Linux)."""
    for _ in range(20):
        try:
            root = json.loads((state / 'process.json').read_text())['pid']
            break
        except FileNotFoundError:
            time.sleep(.025)
    else:
        return False
    pid = os.getppid()
    while pid > 1:
        if pid == root:
            return True
        proc = Path('/proc') / str(pid)
        if Path(os.readlink(proc / 'exe')).name not in ('sh', 'bash', 'dash', 'zsh', 'fish', 'ksh'):
            return False
        # comm may contain spaces or parentheses; fields after its final ')' are stable.
        pid = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[1])
    return False


def capture():
    """Silent hook; locking protects first ownership as well as later updates."""
    directory = os.environ.get('CODEX_TMUX_RUN')
    if not directory:
        return
    try:
        state = Path(directory)
        if not root_hook(state):
            return
        data = json.load(sys.stdin)
        session_id = str(uuid.UUID(data['session_id']))
        event = data.get('hook_event_name')
        if event not in ('SessionStart', 'UserPromptSubmit'):
            return
        if event == 'SessionStart' and data.get('source') not in ('startup', 'resume', 'clear', 'compact'):
            return
        with (state / 'session.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            mapping = state / 'session.json'
            previous = json.loads(mapping.read_text()) if mapping.exists() else {}
            if previous.get('session_id', session_id) != session_id:
                return
            transcript = data.get('transcript_path')
            if not isinstance(transcript, str) or not transcript:
                transcript = previous.get('transcript_path')
            atomic_json(mapping, {'session_id': session_id, 'transcript_path': transcript})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return


def tmux(*args, check=True):
    return subprocess.run(tmux_command(*args), text=True, capture_output=True, check=check).stdout.rstrip('\n')


def tmux_command(*args):
    return ['tmux', *(['-S', SOCKET, '-f', '/dev/null'] if SOCKET else []), *args]


def worker(state):
    deadline = time.monotonic() + 30
    while not (state / 'ready').exists():
        if not state.exists() or time.monotonic() > deadline:
            return
        time.sleep(.1)
    settings = json.loads((state / 'launch.json').read_text())
    env = dict(os.environ, CODEX_TMUX_RUN=str(state), CODEX_HOME=settings['home'])
    command = shlex.join([sys.executable, str(SELF), '_capture'])
    hook = '[{ hooks = [{ type = "command", command = ' + json.dumps(command) + ', timeout = 5 }] }]'
    argv = [settings['codex'], '-C', settings['cwd'],
            '-c', 'hooks.SessionStart=' + hook,
            '-c', 'hooks.UserPromptSubmit=' + hook]
    print('Status bar: context connects on the first prompt. Review /hooks if still unlinked afterward.', flush=True)
    # Codex owns Ctrl+C. The supervisor must wait for its actual exit, rather
    # than being interrupted along with the child by a terminal SIGINT.
    signal.signal(signal.SIGINT, lambda *_: None)
    try:
        process = subprocess.Popen(argv, env=env)
        atomic_json(state / 'process.json', {'pid': process.pid})
        result = process.wait()
        atomic_json(state / 'exit.json', {'code': result})
    finally:
        # Cleanup must run outside the pane: killing our dedicated server also
        # sends SIGHUP to every process in this pane, including the supervisor.
        subprocess.Popen([sys.executable, str(SELF), '_cleanup', str(state)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)


def install_status(settings):
    session = settings['session']
    for option, value in (('status', 'on'), ('status-position', 'bottom'),
                          ('status-style', 'fg=default,bg=default'),
                          ('status-interval', '5'), ('status-format[0]', '#{E:@codex-usage}')):
        tmux('set-option', '-t', session, option, value)
    tmux('set-option', '-w', '-t', settings['window'], '@codex-usage',
         ' Codex | ctx: prompt | 5h: N/A | 7d: N/A')


def cleanup(state, settings):
    # Only confirmed process/pane exit or explicit launch failure calls this.
    try:
        (state / 'cleanup').mkdir()
    except (FileExistsError, FileNotFoundError):
        return
    try:
        try:
            # Pane EOF confirms tmux has consumed the CLI's final output.
            deadline = time.monotonic() + 1
            while tmux('display-message', '-p', '-t', settings['main_pane'], '#{pane_dead}') != '1':
                if time.monotonic() >= deadline:
                    break
                time.sleep(.025)
            screen = subprocess.run(
                tmux_command('capture-pane', '-p', '-t', settings['main_pane']),
                text=True, capture_output=True, check=True).stdout
            (state / 'exit-output.txt').write_text(screen)
        except Exception:
            traceback.print_exc()
    finally:
        try:
            tmux('kill-server', check=False)
        finally:
            shutil.rmtree(state, ignore_errors=True)


def status_columns(settings):
    # Use the narrowest attached client; detached servers use their window size.
    clients = tmux('list-clients', '-t', settings['session'], '-F', '#{client_width}')
    widths = [int(value) for value in clients.splitlines() if value.isdigit() and int(value) > 0]
    if widths:
        return min(widths)
    return int(tmux('display-message', '-p', '-t', settings['main_pane'], '#{window_width}'))


def watch(state):
    settings = json.loads((state / 'launch.json').read_text())
    reader = LogReader(settings['home'])
    while state.exists():
        # Lifecycle checks are separate from display failures. A failed tmux query
        # is not proof that the pane or the application exited.
        try:
            panes = tmux('list-panes', '-t', settings['window'], '-F', '#{pane_id} #{pane_dead}')
            if settings['main_pane'] + ' 0' not in panes.splitlines():
                cleanup(state, settings)
                return
        except Exception:
            if not state.exists() or not Path(settings['socket']).exists():
                return
            traceback.print_exc()
        try:
            mapping = json.loads((state / 'session.json').read_text()) if (state / 'session.json').exists() else {}
            sid = mapping.get('session_id')
            data = reader.read(sid, mapping.get('transcript_path'))
            status = render_status(data, sid, status_columns(settings), **display_options())
            tmux('set-option', '-w', '-t', settings['window'], '@codex-usage', status)
        except Exception:
            if not state.exists():
                return
            traceback.print_exc()
            try:
                tmux('set-option', '-w', '-t', settings['window'], '@codex-usage',
                     ' Codex | Usage data unavailable (see watcher.log)')
            except Exception:
                traceback.print_exc()
        for _ in range(25):
            if not state.exists():
                return
            time.sleep(.2)


def launch(args):
    global SOCKET
    cwd = Path(args.directory).expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError(f'Not a directory: {cwd}')
    codex = shutil.which(args.codex)
    if not codex or not shutil.which('tmux'):
        raise ValueError('Both codex and tmux must be installed.')
    state = Path(tempfile.mkdtemp(prefix='codex-tmux-'))
    SOCKET = str(state / 'tmux.sock')
    settings = {'cwd': str(cwd), 'codex': codex, 'socket': SOCKET,
                'home': str(Path(os.environ.get('CODEX_HOME') or '~/.codex').expanduser().resolve())}
    # Cleanup unlinks this file; the open handle retains the saved notice.
    with (state / 'exit-output.txt').open('w+') as output:
        launch_session(args, state, settings, output)


def launch_session(args, state, settings, output):
    atomic_json(state / 'launch.json', settings)
    command = shlex.join([sys.executable, str(SELF), '_worker', str(state)])
    try:
        name = 'codex'
        result = tmux('new-session', '-d', '-P', '-F', '#{window_id} #{pane_id} #{session_id}', '-s', name, '-n', 'codex', '-c', settings['cwd'], command)
        target, main, session = result.split()
        settings.update(window=target, main_pane=main, session=session, state=str(state))
        atomic_json(state / 'launch.json', settings)
        tmux('set-option', '-w', '-t', target, 'remain-on-exit', 'on')
        tmux('set-option', '-w', '-t', target, 'remain-on-exit-format', '')
        install_status(settings)
        with (state / 'watcher.log').open('a') as log:
            subprocess.Popen([sys.executable, str(SELF), '_watch', str(state)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        tmux('select-pane', '-t', main)
        (state / 'ready').touch()
    except Exception:
        tmux('kill-server', check=False)
        shutil.rmtree(state, ignore_errors=True)
        raise
    if args.detach:
        print(f'tmux target: {target}\nattach: {shlex.join(tmux_command("attach-session", "-t", name))}\nruntime: {state}')
    else:
        env = dict(os.environ)
        env.pop('TMUX', None)
        subprocess.call(tmux_command('attach-session', '-t', name), env=env)
        output.seek(0)
        notice = output.read()
        if notice:
            print(notice, end='', flush=True)


def main():
    global SOCKET
    if len(sys.argv) > 1 and sys.argv[1] == '_capture':
        capture()
        return
    if len(sys.argv) == 3 and sys.argv[1] in ('_worker', '_watch', '_cleanup'):
        state = Path(sys.argv[2])
        try:
            settings = json.loads((state / 'launch.json').read_text())
        except FileNotFoundError:
            return
        SOCKET = settings.get('socket')
        if sys.argv[1] == '_cleanup':
            cleanup(state, settings)
        else:
            (worker if sys.argv[1] == '_worker' else watch)(state)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', default='.', help='Project directory (default: current directory)')
    parser.add_argument('--codex', default='codex', help='Codex executable path')
    parser.add_argument('--detach', action='store_true', help='Create without attaching')
    args = parser.parse_args()
    try:
        launch(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        parser.exit(1, f'Error: {detail}\n')


if __name__ == '__main__':
    main()
