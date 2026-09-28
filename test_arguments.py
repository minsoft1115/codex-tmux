import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest

import codex_tmux as m


def effective_hooks(argv):
    config = {}
    for option, value in m.cli_tokens(argv):
        if option in ('-c', '--config'):
            key, raw = value.split('=', 1)
            target = config
            parts = key.split('.')
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = tomllib.loads('v=' + raw)['v']
    return config['hooks']


class ArgumentTests(unittest.TestCase):
    def test_hooks_preserve_last_cli_value_and_other_arguments(self):
        argv = ['-c', 'hooks.SessionStart=[{hooks=[{type="command",command="old"}]}]',
                'resume', 'session-id', '--config=hooks={SessionStart=[{matcher="resume",'
                'hooks=[{type="command",command="echo \\\"hi\\\"",timeout=42}]}],'
                'UserPromptSubmit=[{hooks=[{type="command",command="prompt"}]}]}',
                '-C', '/project with spaces', '-m', 'model', '--', 'literal --detach $(echo nope)']
        result = m.with_connection_hooks(argv)
        self.assertEqual(result[:len(argv)-2], argv[:-2])
        self.assertEqual(result[-2:], argv[-2:])
        hooks = effective_hooks(result)
        self.assertEqual(len(hooks['SessionStart']), 2)
        self.assertEqual(hooks['SessionStart'][0]['matcher'], 'resume')
        self.assertEqual(hooks['SessionStart'][0]['hooks'][0]['command'], 'echo "hi"')
        self.assertEqual(hooks['SessionStart'][0]['hooks'][0]['timeout'], 42)
        self.assertEqual(hooks['UserPromptSubmit'][0]['hooks'][0]['command'], 'prompt')

    def test_connection_hook_is_not_duplicated(self):
        once = m.with_connection_hooks(['resume', '--last'])
        twice = m.with_connection_hooks(once)
        for groups in effective_hooks(twice).values():
            self.assertEqual(len(groups), 1)

    def test_matching_hook_does_not_suppress_unconditional_capture(self):
        command = shlex.join([sys.executable, str(m.SELF), '_capture'])
        groups = [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': command}]}]
        argv = ['-chooks.SessionStart=' + m.toml_value(groups), 'resume', '--last']
        self.assertEqual(len(effective_hooks(m.with_connection_hooks(argv))['SessionStart']), 2)

    def test_invalid_hook_declaration_is_not_silently_replaced(self):
        for value in ('false', '"bad"', '[1]'):
            with self.assertRaises(ValueError):
                m.with_connection_hooks(['-c', 'hooks.SessionStart=' + value])

    def test_routing_ignores_option_values_and_literal_prompts(self):
        for argv in (['resume', '--last'], ['fork', 'id'], ['-m', 'exec'],
                     ['-C', 'exec'], ['--', '--help'], ['explain exec'],
                     ['--config', 'model="exec"']):
            self.assertFalse(m.direct_command(argv), argv)
        for argv in (['--version'], ['resume', '--help'], ['exec', 'hi'],
                     ['-m', 'some-model', 'exec', '--json', 'hi'], ['completion', 'bash']):
            self.assertTrue(m.direct_command(argv), argv)

    def test_direct_execution_preserves_stdio_argv_and_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'codex'
            fake.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                            'print(json.dumps(sys.argv[1:]))\n'
                            'print(sys.stdin.read(),end="")\n'
                            'print("stderr",file=sys.stderr)\nsys.exit(7)\n')
            fake.chmod(0o755)
            argv = ['exec', '--json', '--', 'hello $USER with spaces']
            result = subprocess.run([sys.executable, str(m.SELF), '--codex', str(fake), *argv],
                                    input='stdin bytes\n', text=True, capture_output=True)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, json.dumps(argv) + '\nstdin bytes\n')
            self.assertEqual(result.stderr, 'stderr\n')

    def test_resume_hook_connects_before_first_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.toml'
            original = '[[hooks.SessionStart]]\nhooks = [{type="command",command="echo existing"}]\n'
            config.write_text(original)
            fake = root / 'codex'
            fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time, tomllib
root = pathlib.Path(os.environ['CODEX_HOME'])
(root / 'argv.json').write_text(json.dumps(sys.argv[1:]))
hooks = {}
for arg in sys.argv[1:]:
    if arg.startswith('hooks.SessionStart='):
        hooks = tomllib.loads(arg)
for group in hooks['hooks']['SessionStart']:
    for hook in group['hooks']:
        subprocess.run(hook['command'], shell=True, check=True, input=json.dumps({
            'hook_event_name':'SessionStart', 'source':'resume',
            'session_id':'00000000-0000-4000-8000-000000000001',
            'transcript_path':str(root / 'resumed.jsonl')}), text=True)
while not (root / 'stop').exists():
    time.sleep(.025)
''')
            fake.chmod(0o755)
            argv = ['resume', '00000000-0000-4000-8000-000000000001', '-C', str(root),
                    'prompt with spaces; $(touch nope)']
            env = dict(os.environ, CODEX_HOME=directory)
            state = None
            try:
                result = subprocess.run([sys.executable, str(m.SELF), '--codex', str(fake),
                                         '--detach', *argv], env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                state = Path(result.stdout.split('runtime: ')[1].strip())
                deadline = time.monotonic() + 5
                while not (state / 'session.json').exists():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.025)
                mapping = json.loads((state / 'session.json').read_text())
                self.assertEqual(mapping['session_id'], argv[1])
                self.assertEqual(mapping['transcript_path'], str(root / 'resumed.jsonl'))
                actual = json.loads((root / 'argv.json').read_text())
                self.assertEqual(actual[:len(argv)], argv)
                self.assertEqual(config.read_text(), original)
            finally:
                (root / 'stop').touch()
                if state is not None:
                    subprocess.run(['tmux', '-S', str(state / 'tmux.sock'), 'kill-server'],
                                   capture_output=True)


if __name__ == '__main__':
    unittest.main()
