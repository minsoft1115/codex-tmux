"""Kill the worker before the normal lifecycle fixture's readiness boundary."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

import codex_tmux as m
import test_lifecycle as lifecycle


class StartupRecoveryTests(unittest.TestCase):
    def test_worker_kill_before_watcher_and_after_codex_spawn(self):
        for phase in ('before-watcher', 'before-registration', 'after-spawn'):
            with self.subTest(phase=phase):
                fixture = lifecycle.LifecycleTests()
                fixture.setUp()
                launcher = None
                ids = {}
                try:
                    entry = fixture.root / 'entry.py'
                    marker = fixture.root / 'startup-blocked'
                    entry.write_text("""import os, pathlib, signal, sys, time, json
sys.path.insert(0, """ + repr(str(m.SELF.parent)) + """)
import codex_tmux as m
m.SELF = pathlib.Path(__file__).resolve()
phase = """ + repr(phase) + """
def pause(state):
    (state.parent / 'blocked-identity').write_text(json.dumps(m.process_identity(os.getpid())))
    (state.parent / 'startup-blocked').write_text(str(state))
    while not (state.parent / 'release').exists():
        time.sleep(.05)
if len(sys.argv) > 1 and sys.argv[1] == '_worker' and phase == 'before-watcher':
    original = m.start_watcher
    def start(state):
        pause(state)
        return original(state)
    m.start_watcher = start
if len(sys.argv) > 1 and sys.argv[1] == '_exec':
    if phase == 'before-registration':
        original = m.exec_codex
        def run(state):
            # Force the unregistered helper to outlive terminal hangup. It
            # must still refuse to exec Codex after its worker has died.
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            pause(state)
            return original(state)
        m.exec_codex = run
    elif phase == 'after-spawn':
        original = m.atomic_json
        def write(path, value):
            original(path, value)
            if path.name == 'process.json':
                pause(path.parent)
        m.atomic_json = write
m.main()
""")
                    env = dict(os.environ, CODEX_HOME=str(fixture.root), TMPDIR=str(fixture.root))
                    launcher = subprocess.Popen([sys.executable, str(entry), '--detach', '--codex', str(fixture.fake)],
                                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    fixture.wait(marker.exists)
                    state = Path(marker.read_text())
                    fixture.states.append(state)
                    settings = m.read_json(state / 'launch.json')
                    ids = {name: m.read_json(state / (name + '.json'))
                           for name in ('worker', 'watcher', 'process')}
                    ids['server'] = settings['server']
                    ids['helper'] = m.read_json(fixture.root / 'blocked-identity')
                    if phase == 'before-watcher':
                        self.assertFalse(ids['watcher'])
                        self.assertFalse(ids['process'])
                    elif phase == 'before-registration':
                        self.assertFalse(ids['process'])
                        self.assertTrue(m.process_alive(ids['watcher']))
                    else:
                        self.assertTrue(m.process_alive(ids['watcher']))
                        self.assertTrue(m.process_alive(ids['process']))
                    # Detach must not report success while startup is incomplete.
                    self.assertIsNone(launcher.poll())
                    m.stop_process(ids['worker'])
                    (fixture.root / 'release').touch()
                    launcher.communicate(timeout=10)
                    fixture.assert_cleaned(state, ids)
                finally:
                    if launcher is not None:
                        if launcher.poll() is None:
                            launcher.kill()
                        launcher.communicate(timeout=5)
                    for identity in ids.values():
                        m.stop_process(identity)
                    fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
