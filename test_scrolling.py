"""Exercise tmux bindings through a real attached client's terminal input."""
import os
from pathlib import Path
import pty
import select
import shlex
import subprocess
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

import codex_tmux as launcher


class ScrollingTests(unittest.TestCase):
    def test_inline_alternate_and_copy_mode_routing(self):
        with tempfile.TemporaryDirectory(prefix='codex-scroll-test-') as directory:
            root = Path(directory)
            received = root / 'received'
            received.touch()
            program = root / 'terminal.py'
            program.write_text('''import os, tty
tty.setraw(0)
os.write(1, b''.join(b'line %d\\r\\n' % i for i in range(150)))
while True:
    data = os.read(0, 4096)
    if data == b'!':
        os.write(1, b'\\x1b[?1049h')
    elif data == b'@':
        os.write(1, b'\\x1b[?1049l')
    else:
        with open('received', 'ab') as stream:
            stream.write(data)
''')
            env = dict(os.environ, TERM='xterm-256color')
            env.pop('TMUX', None)
            master, slave = pty.openpty()
            termios.tcsetwinsize(slave, (24, 80))
            client = None
            with patch.object(launcher, 'SOCKET', str(root / 'tmux.sock')):
                def state(expression):
                    return launcher.tmux('display-message', '-p', '-t', 'test:0.0', expression)

                def wait_for(predicate):
                    deadline = time.monotonic() + 5
                    while True:
                        # Drain output so redraws cannot block the attached client.
                        if select.select([master], [], [], .02)[0]:
                            os.read(master, 65536)
                        if predicate():
                            return
                        self.assertLess(time.monotonic(), deadline, 'tmux state did not converge')

                def press(data):
                    os.write(master, data)

                try:
                    launcher.tmux('new-session', '-d', '-s', 'test', '-c', directory,
                                  'python3 ' + shlex.quote(str(program)))
                    launcher.install_scrolling({'session': 'test'})
                    self.assertEqual(launcher.tmux('show-options', '-v', '-t', 'test', 'mouse'), 'on')
                    client = subprocess.Popen(
                        launcher.tmux_command('attach-session', '-t', 'test'),
                        stdin=slave, stdout=slave, stderr=slave, env=env)
                    wait_for(lambda: state('#{session_attached}') == '1'
                             and int(state('#{history_size}')) > 100)

                    for mode_keys in ('emacs', 'vi'):
                        launcher.tmux('set-option', '-w', '-t', 'test', 'mode-keys', mode_keys)
                        press(b'\x1b[5~')  # Page Up, without a tmux prefix.
                        wait_for(lambda: state('#{pane_in_mode}') == '1')
                        first_page = int(state('#{scroll_position}'))
                        self.assertGreater(first_page, 5)
                        press(b'\x1b[5~')
                        wait_for(lambda: int(state('#{scroll_position}')) > first_page)
                        press(b'\x1b[6~')
                        wait_for(lambda: state('#{scroll_position}') == str(first_page))
                        press(b'\x1b[6~')
                        wait_for(lambda: state('#{pane_in_mode}') == '0')

                        press(b'\x1b[<64;10;10M')  # SGR mouse wheel up.
                        wait_for(lambda: state('#{scroll_position}') == '5')
                        press(b'\x1b[<64;10;10M')
                        wait_for(lambda: state('#{scroll_position}') == '10')
                        press(b'\x1b[<65;10;10M')
                        wait_for(lambda: state('#{scroll_position}') == '5')
                        press(b'\x1b[<65;10;10M')
                        wait_for(lambda: state('#{pane_in_mode}') == '0')
                        press(b'\x1b[5~')
                        wait_for(lambda: state('#{pane_in_mode}') == '1')
                        press(b'\x1b')
                        wait_for(lambda: state('#{pane_in_mode}') == '0')

                    # Down at the inline tail must not reach the prompt editor.
                    press(b'\x1b[6~\x1b[<65;10;10Mz')
                    wait_for(lambda: received.read_bytes().endswith(b'z'))
                    self.assertEqual(received.read_bytes(), b'z')

                    launcher.tmux('send-keys', '-t', 'test', '!')
                    wait_for(lambda: state('#{alternate_on}') == '1')
                    expected = b'z\x1b[5~\x1b[6~\x1b[A\x1b[B'
                    press(b'\x1b[5~\x1b[6~\x1b[<64;10;10M\x1b[<65;10;10M')
                    wait_for(lambda: len(received.read_bytes()) >= len(expected))
                    self.assertEqual(received.read_bytes(), expected)
                    self.assertEqual(state('#{pane_in_mode}'), '0')

                    # Explicit copy mode takes precedence even over an alternate screen.
                    launcher.tmux('copy-mode', '-t', 'test')
                    press(b'\x1b[5~\x1b[<64;10;10M\x1b')
                    wait_for(lambda: state('#{pane_in_mode}') == '0')
                    press(b'z')
                    wait_for(lambda: received.read_bytes().endswith(b'z'))
                    self.assertEqual(received.read_bytes(), expected + b'z')
                    launcher.tmux('send-keys', '-t', 'test', '@')
                    wait_for(lambda: state('#{alternate_on}') == '0')
                    press(b'\x1b[5~')
                    wait_for(lambda: state('#{pane_in_mode}') == '1')
                finally:
                    launcher.tmux('kill-server', check=False)
                    if client is not None:
                        client.wait(timeout=5)
                    os.close(slave)
                    os.close(master)


if __name__ == '__main__':
    unittest.main()
