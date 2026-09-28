"""Exercise startup against a terminal emulator with delayed/missing OSC replies."""
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import termios
import time
import unittest

import codex_tmux as m
import test_lifecycle as lifecycle


class TerminalStartupTests(unittest.TestCase):
    setUp = lifecycle.LifecycleTests.setUp
    tearDown = lifecycle.LifecycleTests.tearDown
    wait = lifecycle.LifecycleTests.wait
    tmux = lifecycle.LifecycleTests.tmux
    assert_cleaned = lifecycle.LifecycleTests.assert_cleaned

    def start(self, fail_attach=False):
        if fail_attach:
            wrapper = self.bin / 'tmux'
            text = wrapper.read_text().replace('import os,sys\n',
                'import os,sys\nif "attach-session" in sys.argv: sys.exit(1)\n')
            wrapper.write_text(text)
        self.fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, select, termios, time, tty
root = pathlib.Path(os.environ['CODEX_HOME'])
original = termios.tcgetattr(0)
tty.setraw(0, termios.TCSANOW)
os.write(1, b'\\x1b]10;?\\x1b\\\\\\x1b]11;?\\x1b\\\\')
data = b''
end = time.monotonic() + .3
while time.monotonic() < end:
    if select.select([0], [], [], .02)[0]:
        data += os.read(0, 4096)
(root / 'observed.json').write_text(json.dumps({'data':data.hex(), 'lflag':original[3]}))
termios.tcsetattr(0, termios.TCSANOW, original)
print('READY', flush=True)
while not (root / 'stop').exists(): time.sleep(.025)
''')
        master, slave = pty.openpty()
        termios.tcsetwinsize(slave, (24, 100))
        env = dict(os.environ, TERM='xterm-256color', TMUX='', CODEX_HOME=str(self.root),
                   TMPDIR=str(self.root), PATH=str(self.bin) + ':' + os.environ['PATH'])
        client = subprocess.Popen([sys.executable, str(m.SELF), '--codex', str(self.fake),
                                   'resume', 'test-id'], stdin=slave, stdout=slave, stderr=slave,
                                  env=env, start_new_session=True)
        self.clients.append((client, master, slave))
        self.wait(lambda: bool(list(self.root.glob('codex-tmux-*/prepared')))
                  or client.poll() is not None)
        states = list(self.root.glob('codex-tmux-*'))
        self.states.extend(states)
        self.state = states[0] if states else None
        return client, master

    def drive(self, client, master, reply_delay=None, cancel=None):
        start = time.monotonic()
        pending = b''
        replies = set()
        early = False
        while not (self.root / 'observed.json').exists() and client.poll() is None:
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 6)
            if select.select([master], [], [], .02)[0]:
                pending += os.read(master, 65536)
            if self.state and list(self.root.glob('codex-tmux-*/prepared')):
                if not early and elapsed > .1:
                    os.write(master, b'typed early\n')
                    early = True
                if cancel and elapsed > .2:
                    cancel()
                    cancel = None
            if reply_delay is not None and elapsed >= reply_delay:
                for n, color in [(10, b'eeee/eeee/eeee'), (11, b'1111/1111/1111')]:
                    for tail in (b'\x07', b'\x1b\\'):
                        query = b'\x1b]' + str(n).encode() + b';?' + tail
                        if query in pending:
                            os.write(master, b'\x1b]' + str(n).encode() + b';rgb:' + color + b'\x1b\\')
                            pending = pending.replace(query, b'')
                            replies.add(n)
        return time.monotonic() - start, replies

    def test_delayed_palette_is_available_to_codex_without_losing_input(self):
        client, master = self.start()
        elapsed, replies = self.drive(client, master, reply_delay=.45)
        self.assertEqual(replies, {10, 11})
        observed = json.loads((self.root / 'observed.json').read_text())
        data = bytes.fromhex(observed['data'])
        self.assertIn(b']10;rgb:', data)
        self.assertIn(b']11;rgb:', data)
        self.assertIn(b'typed early\n', data)
        self.assertTrue(observed['lflag'] & termios.ICANON)
        self.assertTrue(observed['lflag'] & termios.ECHO)
        self.assertTrue(m.read_json(self.state / 'palette.json')['available'])
        self.assertEqual(len(self.tmux(self.state, 'list-windows').splitlines()), 1)

    def test_missing_palette_has_bounded_fallback(self):
        client, master = self.start()
        elapsed, _ = self.drive(client, master)
        self.assertLess(elapsed, 4)
        observed = json.loads((self.root / 'observed.json').read_text())
        self.assertIn(b'typed early\n', bytes.fromhex(observed['data']))
        self.assertFalse(m.read_json(self.state / 'palette.json')['available'])

    def test_disconnect_before_start_cleans_up(self):
        client, master = self.start()
        self.drive(client, master, cancel=lambda: self.tmux(self.state, 'detach-client', '-s', 'codex'))
        client.wait(timeout=5)
        self.wait(lambda: not self.state.exists())
        self.assertFalse((self.root / 'observed.json').exists())

    def test_ctrl_c_during_preparation_cancels_start(self):
        client, master = self.start()
        self.drive(client, master, cancel=lambda: os.write(master, b'\x03'))
        client.wait(timeout=5)
        self.wait(lambda: not self.state.exists())
        self.assertFalse((self.root / 'observed.json').exists())

    def test_worker_death_during_preparation_cleans_up(self):
        client, master = self.start()
        self.drive(client, master, cancel=lambda: m.stop_process(m.read_json(self.state / 'worker.json')))
        client.wait(timeout=8)
        self.wait(lambda: not self.state.exists())
        self.assertFalse((self.root / 'observed.json').exists())

    def test_attach_failure_never_starts_codex(self):
        client, master = self.start(fail_attach=True)
        self.drive(client, master)
        self.assertNotEqual(client.wait(timeout=5), 0)
        self.assertFalse((self.root / 'observed.json').exists())
        self.wait(lambda: not list(self.root.glob('codex-tmux-*')))


if __name__ == '__main__':
    unittest.main()
