"""Failure recovery checks using isolated tmux servers and temporary runtimes."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

import codex_tmux as m
import test_lifecycle as lifecycle


class RecoveryEdgeTests(unittest.TestCase):
    def setUp(self):
        # Keep the lifecycle fixture out of this module's unittest discovery.
        self.fixture = lifecycle.LifecycleTests('test_codex_kill_and_other_session_survives')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def launch(self):
        state, settings, identities = self.fixture.launch()
        # The fixture cannot read identities once cleanup removes launch.json.
        for identity in identities.values():
            self.addCleanup(m.stop_process, identity)
        return state, settings, identities

    def test_unlinked_socket_still_kills_server_and_preserves_other_run(self):
        state, _, identities = self.launch()
        other, _, other_ids = self.launch()

        (state / 'tmux.sock').unlink()
        m.stop_process(identities['process'])

        self.fixture.wait(lambda: not state.exists())
        self.fixture.wait(lambda: not m.process_alive(identities['server']))
        self.assertTrue(other.exists())
        self.assertTrue(all(m.process_alive(identity) for identity in other_ids.values()))
        self.assertEqual(self.fixture.tmux(other, 'list-sessions', '-F', '#{session_name}'), 'codex')

    def test_replacement_watcher_exits_after_cleanup(self):
        state, _, identities = self.launch()
        m.stop_process(identities['watcher'])
        self.fixture.wait(lambda: (replacement := m.read_json(state / 'watcher.json'))
                          and replacement != identities['watcher'] and m.process_alive(replacement))
        replacement = m.read_json(state / 'watcher.json')
        self.addCleanup(m.stop_process, replacement)

        m.stop_process(identities['process'])
        self.fixture.assert_cleaned(state, identities)
        self.fixture.wait(lambda: not m.process_alive(replacement))

    def test_next_launch_removes_interrupted_partial_tombstone(self):
        state, settings, identities = self.launch()
        marker = self.fixture.root / 'deletion-marker'
        # Keep the existing supervisors from finishing cleanup while the
        # injected cleaner is suspended in directory removal.
        for role in ('worker', 'watcher'):
            os.kill(identities[role]['pid'], signal.SIGSTOP)
        m.stop_process(identities['process'])

        code = """
import os, pathlib, signal, sys
sys.path.insert(0, sys.argv[1])
import codex_tmux as m
state = pathlib.Path(sys.argv[2])
marker = pathlib.Path(sys.argv[3])
settings = m.read_json(state / 'launch.json')
m.SOCKET = settings['socket']
original = m.shutil.rmtree
def pause_removal(path, *args, **kwargs):
    path = pathlib.Path(path)
    (path / 'launch.json').unlink()
    marker.write_text(str(path))
    os.kill(os.getpid(), signal.SIGSTOP)
    return original(path, *args, **kwargs)
m.shutil.rmtree = pause_removal
m.cleanup(state, settings)
"""
        cleaner = subprocess.Popen(
            [sys.executable, '-c', code, str(m.SELF.parent), str(state), str(marker)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.fixture.wait(marker.exists)
            tombstone = Path(marker.read_text())
            self.assertTrue(tombstone.exists())
            self.assertFalse((tombstone / 'launch.json').exists())
        finally:
            cleaner.kill()
            cleaner.wait(timeout=5)
            for role in ('worker', 'watcher'):
                m.stop_process(identities[role])

        self.fixture.wait(lambda: not any(m.process_alive(identity) for identity in identities.values()))
        new_state, _, new_ids = self.launch()
        self.assertFalse(tombstone.exists())
        self.assertTrue(new_state.exists())
        self.assertTrue(all(m.process_alive(identity) for identity in new_ids.values()))


if __name__ == '__main__':
    unittest.main()
