#!/usr/bin/env python3
"""Launch Codex with a session-specific native tmux status bar."""
import argparse
import fcntl
import json
import os
import re
import select
import traceback
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import termios
import time
import tomllib
import tty
import uuid

from usage import LogReader, render_status, display_options

SELF = Path(__file__).resolve()
SOCKET = None

# Options whose next token is a value, not a command or another option.
VALUE_OPTIONS = {'-c', '--config', '-C', '--cd', '-m', '--model', '-p', '--profile',
                 '-s', '--sandbox', '-a', '--ask-for-approval', '-i', '--image',
                 '--add-dir', '--enable', '--disable', '--local-provider',
                 '--remote', '--remote-auth-token'}
DIRECT_COMMANDS = {'exec', 'e', 'review', 'login', 'logout', 'mcp', 'plugin',
                   'app-server', 'remote-control', 'app', 'completion', 'update',
                   'doctor', 'sandbox', 'debug', 'execpolicy', 'apply', 'a', 'queue',
                   'archive', 'delete', 'migrate-rollouts', 'unarchive', 'cloud',
                   'cloud-tasks', 'responses-api-proxy', 'stdio-to-uds', 'exec-server',
                   'features', 'help'}


def cli_tokens(argv):
    """Inspect options without rewriting the argv sent to Codex."""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--':
            return
        if arg in VALUE_OPTIONS:
            if i + 1 >= len(argv):
                raise ValueError(f'Missing value for {arg}')
            yield arg, argv[i + 1]
            i += 2
        elif arg.startswith('--') and '=' in arg:
            yield tuple(arg.split('=', 1))
            i += 1
        elif len(arg) > 2 and arg[:2] in VALUE_OPTIONS:
            yield arg[:2], arg[2:].removeprefix('=')
            i += 1
        else:
            yield arg, None
            i += 1


def direct_command(argv):
    positional = None
    for arg, value in cli_tokens(argv):
        if value is None:
            if arg in ('-h', '--help', '-V', '--version'):
                return True
            if not arg.startswith('-') and positional is None:
                positional = arg
    return positional in DIRECT_COMMANDS


def toml_value(value):
    """Encode the TOML values used in hook declarations without dependencies."""
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + ' = ' + toml_value(v)
                               for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(map(toml_value, value)) + ']'
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value, ensure_ascii=False)
    raise ValueError(f'Unsupported value in CLI hook declaration: {type(value).__name__}')


def with_connection_hooks(argv):
    # File/plugin hooks are discovered independently by Codex. Only reconstruct
    # the CLI layer, where repeated -c keys replace earlier values.
    hooks = {}
    for option, assignment in cli_tokens(argv):
        if option not in ('-c', '--config'):
            continue
        key, separator, raw = assignment.partition('=')
        key = key.strip()
        if key != 'hooks' and not key.startswith('hooks.'):
            continue
        if not separator:
            raise ValueError('Hook config requires key=value')
        value = tomllib.loads('value = ' + raw)['value']
        if key == 'hooks':
            if not isinstance(value, dict):
                raise ValueError('hooks must be a TOML table')
            hooks = value
        else:
            target = hooks
            parts = key.split('.')[1:]
            for part in parts[:-1]:
                if not isinstance(target.get(part), dict):
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value
    command = shlex.join([sys.executable, str(SELF), '_capture'])
    additions = []
    for event in ('SessionStart', 'UserPromptSubmit'):
        groups = hooks.get(event, [])
        if not isinstance(groups, list) or any(not isinstance(g, dict) for g in groups):
            raise ValueError(f'hooks.{event} must be an array of tables')
        duplicate = any(
            not group.get('matcher') and any(
                isinstance(handler, dict) and handler.get('type') == 'command'
                and handler.get('command') == command
                for handler in group.get('hooks', []))
            for group in groups)
        if not duplicate:
            groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 5}]})
        additions.extend(['-c', 'hooks.' + event + '=' + toml_value(groups)])
    # Keep -- and all following prompt text literal. Resume/fork accept -c too.
    boundary = argv.index('--') if '--' in argv else len(argv)
    return argv[:boundary] + additions + argv[boundary:]


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        json.dump(value, stream)
        temporary = Path(stream.name)
    temporary.replace(path)


def process_identity(pid):
    """Linux identity survives PID reuse and reboot; zombies count as exited."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'start': fields[19],
                'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except FileNotFoundError:
        return None


def process_alive(identity):
    return bool(identity) and process_identity(identity['pid']) == identity


def stop_process(identity):
    # Pin the process before checking identity; never signal a reused PID.
    if not identity:
        return
    try:
        fd = os.pidfd_open(identity['pid'])
    except ProcessLookupError:
        return
    try:
        if process_alive(identity):
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        os.close(fd)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def retire_run(state):
    """Rename before deleting: a tombstone needs no files to prove retirement."""
    tombstone = state.with_name('codex-tmux-trash-' + uuid.uuid4().hex)
    try:
        state.rename(tombstone)
    except FileNotFoundError:
        return
    shutil.rmtree(tombstone)


def reap_stale_runs():
    """Only reclaim owned v2 runs with no surviving recorded processes."""
    for state in Path(tempfile.gettempdir()).glob('codex-tmux-*'):
        try:
            info = state.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                continue
            if state.name.startswith('codex-tmux-trash-'):
                suffix = state.name.removeprefix('codex-tmux-trash-')
                if len(suffix) == 32 and uuid.UUID(hex=suffix).hex == suffix:
                    shutil.rmtree(state)
                continue
            settings = read_json(state / 'launch.json')
            if settings.get('runtime_version') != 2 or settings.get('state') != str(state):
                continue
            if settings.get('socket') != str(state / 'tmux.sock'):
                continue
            identities = [settings.get('launcher'), settings.get('server')]
            identities += [read_json(state / name) for name in
                           ('worker.json', 'watcher.json', 'process.json')]
            if any(process_alive(item) for item in identities):
                continue
            # Serialize with any cleanup already in progress. No live server is killed.
            with (state / 'cleanup.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                retire_run(state)
        except (OSError, ValueError, KeyError, TypeError):
            continue


def start_watcher(state):
    with (state / 'watcher.log').open('a') as log:
        process = subprocess.Popen([sys.executable, str(SELF), '_watch', str(state)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    atomic_json(state / 'watcher.json', process_identity(process.pid))
    return process


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
    """Follow root CLI session starts; ignore late prompts from other sessions."""
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
            changed_session = previous.get('session_id', session_id) != session_id
            if changed_session and event != 'SessionStart':
                return
            transcript = data.get('transcript_path')
            if not isinstance(transcript, str) or not transcript:
                # A new thread may not have a rollout yet. Never reuse the old
                # thread's path while waiting for its first token event.
                transcript = None if changed_session else previous.get('transcript_path')
            atomic_json(mapping, {'session_id': session_id, 'transcript_path': transcript})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return


def tmux(*args, check=True):
    return subprocess.run(tmux_command(*args), text=True, capture_output=True, check=check, timeout=3).stdout.rstrip('\n')


def tmux_command(*args):
    return ['tmux', *(['-S', SOCKET, '-f', '/dev/null'] if SOCKET else []), *args]


def exec_codex(state):
    """Register this child before exec, serialized against concurrent cleanup."""
    try:
        with (state / 'cleanup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not state.exists() or not process_alive(read_json(state / 'worker.json')):
                return
            settings = read_json(state / 'launch.json')
            if settings.get('wait_for_client') and not client_connected(settings):
                return
            atomic_json(state / 'process.json', process_identity(os.getpid()))
            (state / 'started').touch()
    except FileNotFoundError:
        return
    os.execv(settings['codex'], [settings['codex'], *settings['argv']])


def client_connected(settings):
    return bool(tmux('list-clients', '-t', settings['session'], '-F', '#{client_name}'))


def probe_palette(state):
    """Query in a separate, unselected window so prompt input is never consumed."""
    original = termios.tcgetattr(0)
    found = set()
    data = b''
    try:
        tty.setraw(0, termios.TCSANOW)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and found != {b'10', b'11'}:
            os.write(1, b'\x1b]10;?\x1b\\\x1b]11;?\x1b\\')
            if select.select([0], [], [], .05)[0]:
                chunk = os.read(0, 4096)
                if not chunk:
                    break
                data = (data + chunk)[-8192:]
                found.update(re.findall(
                    rb'\x1b\](10|11);rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/'
                    rb'[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)', data))
    finally:
        termios.tcsetattr(0, termios.TCSANOW, original)
    atomic_json(state / 'palette.json', {'available': found == {b'10', b'11'}})


def prepare_terminal(state, settings):
    deadline = time.monotonic() + 10
    while not client_connected(settings):
        if not process_alive(settings.get('launcher')) or time.monotonic() >= deadline:
            raise ValueError('Terminal did not attach during startup.')
        time.sleep(.025)
    # tmux caches the client's OSC replies. Probe through another window, leaving
    # the main pane's terminal modes and queued keystrokes entirely untouched.
    command = shlex.join([sys.executable, str(SELF), '_palette', str(state)])
    window = tmux('new-window', '-d', '-P', '-F', '#{window_id}',
                  '-t', settings['session'] + ':', '-n', 'palette', command)
    try:
        deadline = time.monotonic() + 2
        while not (state / 'palette.json').exists() and time.monotonic() < deadline:
            if not client_connected(settings):
                raise ValueError('Terminal disconnected during startup.')
            time.sleep(.025)
        if not client_connected(settings):
            raise ValueError('Terminal disconnected during startup.')
    finally:
        tmux('kill-window', '-t', window, check=False)


def worker(state):
    atomic_json(state / 'worker.json', process_identity(os.getpid()))
    deadline = time.monotonic() + 30
    while not (state / 'ready').exists():
        if not state.exists() or time.monotonic() > deadline:
            return
        time.sleep(.1)
    settings = json.loads((state / 'launch.json').read_text())
    env = dict(os.environ, CODEX_TMUX_RUN=str(state), CODEX_HOME=settings['home'])
    print('Status bar: context connects on the first prompt. Review /hooks if still unlinked afterward.', flush=True)
    try:
        # Establish supervision before announcing readiness to attach. Detached
        # launchers additionally wait for Codex's process registration.
        watcher = start_watcher(state)
        (state / 'prepared').touch()
        if settings.get('wait_for_client'):
            prepare_terminal(state, settings)
        # Once Codex starts, it owns Ctrl+C. During preparation it cancels startup.
        signal.signal(signal.SIGINT, lambda *_: None)
        process = subprocess.Popen([sys.executable, str(SELF), '_exec', str(state)], env=env)
        while process.poll() is None:
            if watcher.poll() is not None:
                watcher = start_watcher(state)
            time.sleep(.2)
        result = process.returncode
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


def install_scrolling(settings):
    """Honor native mouse handling; provide scrolling for keyboard-only screens."""
    tmux('set-option', '-t', settings['session'], 'mouse', 'on')
    # Mouse targets follow the event; keyboard targets follow the active pane.
    tmux('bind-key', '-T', 'root', 'WheelUpPane',
         'if-shell', '-F', '-t', '=', '#{||:#{pane_in_mode},#{mouse_any_flag}}', 'send-keys -M',
         'if-shell -F -t = "#{alternate_on}" "send-keys -t = Up" '
         '"copy-mode -e -t =; send-keys -t = -X -N 5 scroll-up"')
    tmux('bind-key', '-T', 'root', 'WheelDownPane',
         'if-shell', '-F', '-t', '=', '#{||:#{pane_in_mode},#{mouse_any_flag}}', 'send-keys -M',
         'if-shell -F -t = "#{alternate_on}" "send-keys -t = Down"')
    tmux('bind-key', '-T', 'root', 'PPage',
         'if-shell', '-F', '#{alternate_on}', 'send-keys PPage', 'copy-mode -e -u')
    tmux('bind-key', '-T', 'root', 'NPage',
         'if-shell', '-F', '#{alternate_on}', 'send-keys NPage')
    for table in ('copy-mode', 'copy-mode-vi'):
        tmux('bind-key', '-T', table, 'PPage', 'send-keys', '-X', 'page-up')
        tmux('bind-key', '-T', table, 'NPage', 'send-keys', '-X', 'page-down', ';',
             'if-shell', '-F', '#{==:#{scroll_position},0}', 'send-keys -X cancel')
        tmux('bind-key', '-T', table, 'WheelUpPane',
             'send-keys', '-X', '-N', '5', 'scroll-up')
        tmux('bind-key', '-T', table, 'WheelDownPane',
             'send-keys', '-X', '-N', '5', 'scroll-down', ';',
             'if-shell', '-F', '#{==:#{scroll_position},0}', 'send-keys -X cancel')
        tmux('bind-key', '-T', table, 'Escape', 'send-keys', '-X', 'cancel')


def disable_prefix_bindings(settings):
    """Disable tmux command prefixes in this run's private server only."""
    session = settings['session']
    tmux('set-option', '-t', session, 'prefix', 'None')
    tmux('set-option', '-t', session, 'prefix2', 'None')
    tmux('unbind-key', '-a', '-T', 'prefix')


def cleanup(state, settings):
    # flock is released by the kernel even if the cleanup owner is SIGKILLed.
    try:
        lock = (state / 'cleanup.lock').open('a')
    except FileNotFoundError:
        return
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if not state.exists():
            return
        try:
            if not (state / 'exit-output.txt').exists() or not (state / 'exit-output.txt').stat().st_size:
                try:
                    deadline = time.monotonic() + 1
                    while tmux('display-message', '-p', '-t', settings['main_pane'], '#{pane_dead}') != '1':
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(.025)
                    screen = subprocess.run(
                        tmux_command('capture-pane', '-p', '-e', '-t', settings['main_pane']),
                        text=True, capture_output=True, check=True, timeout=3).stdout
                    (state / 'exit-output.txt').write_text(screen)
                except Exception:
                    traceback.print_exc()
        finally:
            try:
                tmux('kill-server', check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            finally:
                # Let a responsive server finish restoring attached terminals.
                deadline = time.monotonic() + .5
                while process_alive(settings.get('server')) and time.monotonic() < deadline:
                    time.sleep(.025)
                # Missing sockets and nonzero command exits are not success.
                identities = [settings.get('server'), read_json(state / 'process.json')]
                for identity in identities:
                    stop_process(identity)
                deadline = time.monotonic() + 3
                while any(process_alive(identity) for identity in identities):
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(.025)
                retire_run(state)


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
        # Bound repeated diagnostics, including transient display failures.
        log = state / 'watcher.log'
        try:
            if log.exists() and log.stat().st_size > 256 * 1024:
                with log.open('w'):
                    pass
        except FileNotFoundError:
            return
        if settings.get('runtime_version') == 2 and (
                not process_alive(settings.get('server'))
                or not process_alive(read_json(state / 'worker.json'))
                or (state / 'exit.json').exists()):
            cleanup(state, settings)
            if not state.exists():
                return
            time.sleep(.2)
            continue
        # Lifecycle checks are separate from display failures. A failed tmux query
        # is not proof that the pane or the application exited.
        try:
            panes = tmux('list-panes', '-t', settings['window'], '-F', '#{pane_id} #{pane_dead}')
            if settings['main_pane'] + ' 0' not in panes.splitlines():
                if cleanup(state, settings) is not False:
                    return
        except Exception:
            if not state.exists() or (settings.get('runtime_version') != 2
                                      and not Path(settings['socket']).exists()):
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
    cwd = Path.cwd()
    codex = shutil.which(args.codex)
    if not codex:
        raise ValueError('Codex must be installed and on PATH.')
    if Path(codex).resolve() in (SELF, SELF.parent.parent.parent / 'bin' / 'codex-tmux'):
        raise ValueError('The Codex executable must not point to codex-tmux.')
    if direct_command(args.argv) or (not args.detach and not sys.stdin.isatty()):
        os.execv(codex, [codex, *args.argv])
    if not shutil.which('tmux'):
        raise ValueError('tmux must be installed.')
    # Herdr otherwise sees the nested tmux client as the foreground process.
    # This is scoped to this wrapper process and its children, not the shell.
    os.environ['HERDR_AGENT'] = 'codex'
    reap_stale_runs()
    argv = with_connection_hooks(args.argv)
    state = Path(tempfile.mkdtemp(prefix='codex-tmux-'))
    SOCKET = str(state / 'tmux.sock')
    settings = {'runtime_version': 2, 'launcher': process_identity(os.getpid()),
                'wait_for_client': not args.detach,
                'state': str(state), 'cwd': str(cwd), 'codex': codex, 'socket': SOCKET, 'argv': argv,
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
        settings.update(window=target, main_pane=main, session=session, state=str(state),
                        server=process_identity(int(tmux('display-message', '-p', '#{pid}'))))
        atomic_json(state / 'launch.json', settings)
        tmux('set-option', '-w', '-t', target, 'remain-on-exit', 'on')
        tmux('set-option', '-w', '-t', target, 'remain-on-exit-format', '')
        install_status(settings)
        disable_prefix_bindings(settings)
        install_scrolling(settings)
        tmux('select-pane', '-t', main)
        pane_process = process_identity(int(tmux('display-message', '-p', '-t', main, '#{pane_pid}')))
        (state / 'ready').touch()
        deadline = time.monotonic() + 10
        milestone = 'started' if args.detach else 'prepared'
        while state.exists() and not (state / milestone).exists():
            identity = read_json(state / 'worker.json') or pane_process
            if not process_alive(identity):
                raise ValueError('Codex worker exited during startup.')
            if time.monotonic() >= deadline:
                raise ValueError('Codex worker did not finish startup.')
            time.sleep(.025)
    except BaseException:
        cleanup(state, settings)
        raise
    if not state.exists():
        output.seek(0)
        print(output.read(), end='', flush=True)
        return
    if args.detach:
        print(f'tmux target: {target}\nattach: {shlex.join(tmux_command("attach-session", "-t", name))}\nruntime: {state}')
    else:
        env = dict(os.environ)
        env.pop('TMUX', None)
        try:
            result = subprocess.call(tmux_command('attach-session', '-t', name), env=env)
        finally:
            startup_incomplete = state.exists() and not (state / 'started').exists()
            if startup_incomplete:
                cleanup(state, settings)
        # kill-server also makes an attached tmux client return nonzero on a
        # normal Codex exit; only treat a failed initial attach as an error.
        if result and startup_incomplete:
            raise ValueError('Could not attach to the Codex terminal.')
        output.seek(0)
        notice = output.read()
        if notice:
            print(notice, end='', flush=True)


def main():
    global SOCKET
    if len(sys.argv) > 1 and sys.argv[1] == '_capture':
        capture()
        return
    if len(sys.argv) == 3 and sys.argv[1] in ('_worker', '_watch', '_cleanup', '_exec', '_palette'):
        state = Path(sys.argv[2])
        try:
            settings = json.loads((state / 'launch.json').read_text())
        except FileNotFoundError:
            return
        SOCKET = settings.get('socket')
        if sys.argv[1] == '_palette':
            probe_palette(state)
        elif sys.argv[1] == '_exec':
            exec_codex(state)
        elif sys.argv[1] == '_cleanup':
            cleanup(state, settings)
        else:
            (worker if sys.argv[1] == '_worker' else watch)(state)
        return
    parser = argparse.ArgumentParser(description=__doc__, add_help=False, allow_abbrev=False,
                                     usage='%(prog)s [--codex PATH] [--detach] [CODEX ARGS...]')
    parser.add_argument('--tmux-help', action='help', help='Show wrapper help; --help goes to Codex')
    parser.add_argument('--codex', default='codex', help='Codex executable path')
    parser.add_argument('--detach', action='store_true', help='Create without attaching')
    # Wrapper options must precede Codex arguments. From the first Codex token
    # onward preserve every argument (including --) exactly.
    boundary = 1
    while boundary < len(sys.argv):
        token = sys.argv[boundary]
        if token == '--codex':
            boundary += 2
        elif token in ('--detach', '--tmux-help') or token.startswith('--codex='):
            boundary += 1
        else:
            break
    args = parser.parse_args(sys.argv[1:boundary])
    args.argv = sys.argv[boundary:]
    try:
        launch(args)
    except KeyboardInterrupt:
        parser.exit(130)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        parser.exit(1, f'Error: {detail}\n')


if __name__ == '__main__':
    main()
