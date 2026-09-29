"""Real isolated tmux/SIGKILL scenarios; never use the caller's tmux server."""
import json
import os
from pathlib import Path
import pty
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import codex_tmux as m


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lifecycle-test-')
        self.root = Path(self.temp.name)
        self.states = []
        self.clients = []
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.failure = self.root / 'fail-display'
        wrapper = self.bin / 'tmux'
        wrapper.write_text('#!/usr/bin/python3\nimport os,sys\n'
                           + f'if os.path.exists({str(self.failure)!r}) and "list-clients" in sys.argv: sys.exit(1)\n'
                           + f'os.execv({shutil.which("tmux")!r}, ["tmux", *sys.argv[1:]])\n')
        wrapper.chmod(0o755)
        self.fake = self.root / 'fake-codex'
        self.fake.write_text('''#!/usr/bin/env python3
import os, pathlib, signal, time
import json
# Also exercise orphan cleanup when Codex ignores terminal hangup.
pathlib.Path(os.environ['CODEX_HOME'], 'herdr-agent').write_text(os.environ.get('HERDR_AGENT', 'unset'))
pathlib.Path(os.environ['CODEX_HOME'], 'herdr-context.json').write_text(json.dumps({
    key: os.environ.get(key) for key in ('HERDR_ENV', 'HERDR_SOCKET_PATH', 'HERDR_PANE_ID')}))
signal.signal(signal.SIGHUP, signal.SIG_IGN)
print('fake running', flush=True)
while not (pathlib.Path(os.environ['CODEX_HOME']) / 'stop').exists():
    time.sleep(.05)
''')
        self.fake.chmod(0o755)

    def wait(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('Timed out waiting for lifecycle transition')
            time.sleep(.05)

    def launch(self):
        # Keep startup garbage collection confined to this test's runtimes.
        env = dict(os.environ, CODEX_HOME=str(self.root), TMPDIR=str(self.root),
                   PATH=str(self.bin) + ':' + os.environ['PATH'], HERDR_AGENT='outer-value',
                   HERDR_ENV='1', HERDR_SOCKET_PATH='/tmp/herdr-test.sock', HERDR_PANE_ID='w1:p1')
        result = subprocess.run([sys.executable, str(m.SELF), '--detach', '--codex', str(self.fake)],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = Path(result.stdout.split('runtime: ')[1].strip())
        self.states.append(state)
        self.wait(lambda: (state / 'watcher.json').exists())
        self.wait(lambda: (self.root / 'herdr-agent').exists())
        self.wait(lambda: (self.root / 'herdr-context.json').exists())
        settings = m.read_json(state / 'launch.json')
        self.assertEqual((self.root / 'herdr-agent').read_text(), 'codex')
        self.assertEqual(json.loads((self.root / 'herdr-context.json').read_text()), {
            'HERDR_ENV': '1', 'HERDR_SOCKET_PATH': '/tmp/herdr-test.sock', 'HERDR_PANE_ID': 'w1:p1'})
        identities = {name: m.read_json(state / (name + '.json')) for name in ('worker', 'watcher', 'process')}
        identities['server'] = settings['server']
        return state, settings, identities

    def tmux(self, state, *args):
        return subprocess.check_output(['tmux', '-S', str(state / 'tmux.sock'), *args], text=True, timeout=5).strip()

    def tearDown(self):
        (self.root / 'stop').touch()
        for client, master, slave in self.clients:
            if client.poll() is None:
                client.kill()
            client.wait(timeout=5)
            os.close(master)
            os.close(slave)
        for state in self.states:
            settings = m.read_json(state / 'launch.json')
            identities = [m.read_json(state / (name + '.json')) for name in ('worker', 'watcher', 'process')]
            subprocess.run(['tmux', '-S', str(state / 'tmux.sock'), 'kill-server'], capture_output=True, timeout=5)
            for identity in identities + [settings.get('server')]:
                m.stop_process(identity)
        self.temp.cleanup()

    def assert_cleaned(self, state, identities):
        self.wait(lambda: not state.exists())
        self.wait(lambda: not any(m.process_alive(i) for i in identities.values()))

    def test_codex_kill_and_other_session_survives(self):
        state, _, ids = self.launch()
        other, _, others = self.launch()
        m.stop_process(ids['process'])
        self.assert_cleaned(state, ids)
        self.assertTrue(all(m.process_alive(i) for i in others.values()))
        self.assertEqual(self.tmux(other, 'list-sessions', '-F', '#{session_name}'), 'codex')

    def test_worker_kill_cleans_hangup_ignoring_codex(self):
        state, _, ids = self.launch()
        m.stop_process(ids['worker'])
        self.assert_cleaned(state, ids)

    def test_server_kill_cleans_stale_socket_and_watcher(self):
        state, _, ids = self.launch()
        m.stop_process(ids['server'])
        self.assert_cleaned(state, ids)

    def test_watcher_restarts_and_refreshes_status(self):
        state, _, ids = self.launch()
        self.tmux(state, 'set-option', '-w', '@codex-usage', 'frozen sentinel')
        m.stop_process(ids['watcher'])
        self.wait(lambda: m.read_json(state / 'watcher.json') != ids['watcher'])
        self.wait(lambda: 'ctx:' in self.tmux(state, 'show-options', '-wv', '@codex-usage'))
        self.assertTrue(m.process_alive(ids['process']))
        m.stop_process(ids['process'])
        self.assert_cleaned(state, ids)

    def test_killed_cleanup_owner_is_replaced(self):
        # Stop the actual cleanup before capture, before server shutdown, and
        # after shutdown. A second cleaner must resume all three cases.
        for phase in ('display-message', 'kill-server', 'after-kill'):
            with self.subTest(phase=phase):
                state, _, ids = self.launch()
                os.kill(ids['watcher']['pid'], signal.SIGSTOP)
                code = """
import os, pathlib, signal, sys
sys.path.insert(0, sys.argv[1])
import codex_tmux as m
state = pathlib.Path(sys.argv[2])
settings = m.read_json(state / 'launch.json')
m.SOCKET = settings['socket']
original = m.tmux
phase = sys.argv[3]
def pause():
    (state / 'locked').touch()
    os.kill(os.getpid(), signal.SIGSTOP)
def command(*args, **kwargs):
    if args[0] == phase:
        pause()
    result = original(*args, **kwargs)
    if args[0] == 'kill-server' and phase == 'after-kill':
        pause()
    return result
m.tmux = command
m.cleanup(state, settings)
"""
                cleaner = subprocess.Popen([sys.executable, '-c', code, str(m.SELF.parent), str(state), phase],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    self.wait(lambda: (state / 'locked').exists())
                    m.stop_process(ids['process'])
                    cleaner.kill()
                    cleaner.wait(timeout=5)
                    os.kill(ids['watcher']['pid'], signal.SIGCONT)
                    self.assert_cleaned(state, ids)
                finally:
                    if cleaner.poll() is None:
                        cleaner.kill()
                    cleaner.wait(timeout=5)
                    if m.process_alive(ids['watcher']):
                        os.kill(ids['watcher']['pid'], signal.SIGCONT)

    def test_client_kill_detach_and_reattach(self):
        state, _, ids = self.launch()
        for iteration in range(2):
            master, slave = pty.openpty()
            client = subprocess.Popen(['tmux', '-S', str(state / 'tmux.sock'), 'attach-session', '-t', 'codex'],
                                      stdin=slave, stdout=slave, stderr=slave,
                                      env=dict(os.environ, TERM='xterm-256color', TMUX=''), start_new_session=True)
            self.clients.append((client, master, slave))
            self.wait(lambda: bool(self.tmux(state, 'list-clients', '-F', '#{client_pid}')))
            if iteration == 0:
                client.kill()
            else:
                self.tmux(state, 'detach-client', '-s', 'codex')
            client.wait(timeout=5)
            self.assertTrue(all(m.process_alive(i) for i in ids.values()))
        m.stop_process(ids['process'])
        self.assert_cleaned(state, ids)

    def test_next_launch_reaps_all_killed_run_but_keeps_live_run(self):
        state, _, ids = self.launch()
        other, _, others = self.launch()
        # Freeze both supervisors before killing everything, so no cleanup runs.
        for role in ('worker', 'watcher'):
            os.kill(ids[role]['pid'], signal.SIGSTOP)
        for identity in ids.values():
            m.stop_process(identity)
        self.wait(lambda: not any(m.process_alive(i) for i in ids.values()))
        self.assertTrue(state.exists())
        self.launch()
        self.assertFalse(state.exists())
        self.assertTrue(other.exists())
        self.assertTrue(all(m.process_alive(i) for i in others.values()))

    def test_display_errors_bound_log_and_recover(self):
        state, _, ids = self.launch()
        self.failure.touch()
        self.wait(lambda: 'unavailable' in self.tmux(state, 'show-options', '-wv', '@codex-usage'))
        log = state / 'watcher.log'
        with log.open('a') as stream:
            stream.write('previous repeated diagnostics\n' * 12000)
        self.wait(lambda: log.stat().st_size < 256 * 1024)
        self.assertTrue(all(m.process_alive(i) for i in ids.values()))
        self.failure.unlink()
        self.wait(lambda: 'ctx:' in self.tmux(state, 'show-options', '-wv', '@codex-usage'))
        m.stop_process(ids['process'])
        self.assert_cleaned(state, ids)

    def test_reused_pid_is_not_signalled(self):
        identity = dict(m.process_identity(os.getpid()), start='invalid')
        with patch.object(m.signal, 'pidfd_send_signal') as send:
            m.stop_process(identity)
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
