import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

SCRIPT = Path(__file__).with_name('codex_tmux.py')
spec = importlib.util.spec_from_file_location('launcher', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Tests(unittest.TestCase):
    def event(self, used=250, limits=None, timestamp='2026-09-21T01:00:00Z'):
        return json.dumps({'timestamp': timestamp, 'type': 'event_msg', 'payload': {
            'type': 'token_count', 'info': {'last_token_usage': {'total_tokens': used},
            'model_context_window': 1000}, 'rate_limits': limits}}) + '\n'

    def test_incremental_logs_and_scoped_context(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            logs = home / 'sessions'
            logs.mkdir()
            selected, other = logs / 'selected.jsonl', logs / 'other.jsonl'
            meta = json.dumps({'type': 'session_meta', 'payload': {'id': 'selected'}}) + '\n'
            selected.write_text(meta + self.event(limits={'secondary': {'window_minutes': 10080, 'used_percent': 42}}))
            other.write_text(self.event(900, {'primary': {'window_minutes': 300, 'used_percent': 17}}, '2026-09-21T02:00:00Z'))
            reader = m.LogReader(home)
            data = reader.read('selected', str(selected))
            self.assertEqual(data, {'context': 25, 'limits': {300: 17, 10080: 42}})
            self.assertIsNone(reader.read()['context'])
            self.assertIsNone(reader.read('wrong', str(selected))['context'])
            # Unchanged files are not reopened.
            with patch.object(Path, 'open', side_effect=AssertionError('reread')):
                self.assertEqual(reader.read('selected'), data)
            appended = self.event(500, timestamp='2026-09-21T03:00:00Z')
            with selected.open('a') as stream:
                stream.write('not json\n[]\n' + appended[:-2])
            self.assertEqual(reader.read('selected')['context'], 25)
            with selected.open('a') as stream:
                stream.write(appended[-2:])
            self.assertEqual(reader.read('selected')['context'], 50)
            other.unlink()
            self.assertNotIn(300, reader.read('selected')['limits'])
            selected.write_text(meta + self.event(100))
            self.assertEqual(reader.read('selected')['context'], 10)
            replacement = logs / 'replacement'
            replacement.write_text(meta + self.event(800))
            replacement.replace(selected)
            self.assertEqual(reader.read('selected')['context'], 80)

    def test_invalid_values_and_latest_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory) / 'sessions'
            logs.mkdir()
            path = logs / 'a.jsonl'
            path.write_text(self.event(limits={'primary': {'window_minutes': 300, 'used_percent': 60}})
                            + self.event(limits={'primary': {'window_minutes': 300, 'used_percent': 10}}, timestamp='2026-09-20T01:00:00Z')
                            + self.event(limits={'primary': {'window_minutes': [], 'used_percent': float('nan')}}))
            self.assertEqual(m.LogReader(directory).read()['limits'], {300: 60})

    def test_render_fits_width_and_color(self):
        import re
        data = {'context': 25, 'limits': {300: 17, 10080: 42}}
        for columns in range(1, 201):
            text = m.render_status(data, '12345678-abcd', columns)
            plain = re.sub(r'#\[[^]]*\]', '', text)
            self.assertLessEqual(len(plain), columns)
            if columns >= 30:
                for value in ('25%', '17%', '42%'):
                    self.assertIn(value, plain)
        for columns in (40, 50, 60, 80):
            text = m.render_status(data, '12345678-abcd', columns, color=False)
            self.assertGreaterEqual(text.count('░'), 3)
            self.assertEqual(text.count('%'), 3)
        colored = m.render_status(data, None, 100)
        self.assertIn('#[fg=green]▓', colored)
        self.assertIn('#[fg=default]░', colored)
        self.assertNotIn('bg=', colored)
        self.assertNotIn('#[', m.render_status(data, None, 100, color=False))
        self.assertIn('N/A', m.render_status({'context': None, 'limits': {}}, None, 80))
        with patch.dict(os.environ, {'NO_COLOR': '', 'CODEX_TMUX_BAR_WIDTH': 'bad'}):
            self.assertFalse(m.display_options()['color'])
            self.assertEqual(m.display_options()['bar_width'], 10)

    def test_width_uses_tmux_clients_and_detached_window(self):
        settings = {'session': '$0', 'main_pane': '%0'}
        with patch.object(m, 'tmux', return_value='120\n55'):
            self.assertEqual(m.status_columns(settings), 55)
        with patch.object(m, 'tmux', side_effect=['', '93']):
            self.assertEqual(m.status_columns(settings), 93)

    def test_watcher_errors_do_not_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'launch.json').write_text(json.dumps({'home': directory, 'window': '@0', 'main_pane': '%0', 'socket': str(state / 'tmux.sock')}))
            calls = 0
            statuses = []
            def command(*args, **kwargs):
                nonlocal calls
                if args[0] == 'list-panes':
                    calls += 1
                    if calls == 4:
                        raise KeyboardInterrupt
                    return '%0 0'
                if args[0] == 'set-option':
                    statuses.append(args[-1])
                return ''
            with patch.object(m, 'tmux', side_effect=command), patch.object(m, 'cleanup') as cleanup, \
                 patch.object(m.LogReader, 'read', side_effect=[AttributeError('broken'), KeyError('bad'), {'context': 25, 'limits': {}}]), \
                 patch.object(m, 'status_columns', return_value=80), \
                 patch.object(m.time, 'sleep'), patch.object(m.traceback, 'print_exc') as logged:
                with self.assertRaises(KeyboardInterrupt):
                    m.watch(state)
                cleanup.assert_not_called()
                self.assertEqual(logged.call_count, 2)
                self.assertIn('25%', statuses[-1])

    def test_tmux_query_failure_is_not_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            settings = {'home': directory, 'window': '@0', 'main_pane': '%0', 'socket': str(state / 'tmux.sock')}
            (state / 'launch.json').write_text(json.dumps(settings))
            (state / 'tmux.sock').touch()
            calls = 0
            def command(*args, **kwargs):
                nonlocal calls
                if args[0] == 'list-panes':
                    calls += 1
                    if calls == 1:
                        raise subprocess.CalledProcessError(1, 'tmux')
                    raise KeyboardInterrupt
                return ''
            with patch.object(m, 'tmux', side_effect=command), patch.object(m, 'cleanup') as cleanup, \
                 patch.object(m, 'status_columns', return_value=80), patch.object(m.time, 'sleep'), \
                 patch.object(m.traceback, 'print_exc') as logged:
                with self.assertRaises(KeyboardInterrupt):
                    m.watch(state)
                cleanup.assert_not_called()
                logged.assert_called_once()
            with patch.object(m, 'tmux', return_value='%0 1'), patch.object(m, 'cleanup') as cleanup:
                m.watch(state)
                cleanup.assert_called_once_with(state, settings)

    def test_concurrent_capture_and_nested_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'process.json').write_text(json.dumps({'pid': os.getpid()}))
            env = dict(os.environ, CODEX_TMUX_RUN=directory)
            ids = [str(uuid.uuid4()) for _ in range(8)]
            procs = [subprocess.Popen([sys.executable, str(SCRIPT), '_capture'], env=env,
                                      stdin=subprocess.PIPE, text=True) for _ in ids]
            for proc, sid in zip(procs, ids):
                proc.stdin.write(json.dumps({'session_id': sid, 'hook_event_name': 'UserPromptSubmit', 'transcript_path': '/first'}))
                proc.stdin.close()
            for proc in procs:
                self.assertEqual(proc.wait(), 0)
            saved = json.loads((state / 'session.json').read_text())
            self.assertIn(saved['session_id'], ids)
            payload = json.dumps({'session_id': saved['session_id'], 'hook_event_name': 'UserPromptSubmit'})
            subprocess.run([sys.executable, str(SCRIPT), '_capture'], env=env, input=payload, text=True, check=True)
            self.assertEqual(json.loads((state / 'session.json').read_text())['transcript_path'], '/first')
            (state / 'session.json').unlink()
            # A nested CLI/tool inherits the environment but must not claim the root mapping.
            code = "import subprocess,sys; subprocess.run([sys.executable,sys.argv[1],'_capture'], input=sys.argv[2],text=True,check=True)"
            subprocess.run([sys.executable, '-c', code, str(SCRIPT), payload], env=env, check=True)
            self.assertFalse((state / 'session.json').exists())

    def test_tmux_isolation_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='codex-tmux-test-') as directory:
            root = Path(directory)
            socket = 'codex-test-' + uuid.uuid4().hex
            bins = root / 'bin'
            bins.mkdir()
            wrapper = bins / 'tmux'
            wrapper.write_text(f'#!/bin/sh\nif [ "$1" = -S ]; then exec /usr/bin/tmux "$@"; fi\nexec /usr/bin/tmux -L {socket} -f /dev/null "$@"\n')
            wrapper.chmod(0o755)
            fake = bins / 'fake-codex'
            fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time, tomllib, uuid
sid = str(uuid.uuid4())
root = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions'
root.mkdir(parents=True, exist_ok=True)
log = root / (sid + '.jsonl')
log.write_text(json.dumps({'type':'session_meta','payload':{'id':sid}})+'\\n'+json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{'last_token_usage':{'total_tokens':250},'model_context_window':1000}}})+'\\n')
for arg in sys.argv:
    if arg.startswith('hooks.SessionStart='):
        command = tomllib.loads(arg)['hooks']['SessionStart'][0]['hooks'][0]['command']
        subprocess.run(command, shell=True, input=json.dumps({'session_id':sid,'hook_event_name':'SessionStart','source':'startup','transcript_path':str(log)}), text=True, check=True)
print('FAKE CODEX ' + sid, flush=True)
while not (pathlib.Path(os.environ['CODEX_HOME']) / 'stop').exists():
    time.sleep(.1)
''')
            fake.chmod(0o755)
            env = dict(os.environ, PATH=str(bins)+':'+os.environ['PATH'], CODEX_HOME=str(root))
            env.pop('TMUX', None)
            states = []
            try:
                subprocess.run([str(wrapper), 'new-session', '-d', '-s', 'keeper', 'sleep 120'], env=env, check=True)
                subprocess.run([str(wrapper), 'set-option', '-t', 'keeper', 'status', '2'], env=env, check=True)
                subprocess.run([str(wrapper), 'set-option', '-t', 'keeper', 'status-position', 'top'], env=env, check=True)
                subprocess.run([str(wrapper), 'set-option', '-t', 'keeper', 'status-format[0]', 'original status'], env=env, check=True)
                subprocess.run([str(wrapper), 'set-option', '-t', 'keeper', 'status-format[1]', 'second line'], env=env, check=True)
                env['TMUX'] = subprocess.check_output([str(wrapper), 'display-message', '-p', '-t', 'keeper', '#{socket_path}'], env=env, text=True).strip() + ',0,0'
                for iteration in range(2):
                    if iteration == 1:
                        env.pop('TMUX', None)
                    result = subprocess.run([sys.executable, str(SCRIPT), str(root), '--codex', str(fake), '--detach'], env=env, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    states.append(Path(result.stdout.split('runtime: ')[1].strip()))
                deadline = time.monotonic() + 15
                while not all((state / 'session.json').exists() for state in states):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.1)
                ids = [json.loads((state / 'session.json').read_text())['session_id'] for state in states]
                self.assertNotEqual(*ids)
                time.sleep(5.5)
                configs = [json.loads((state / 'launch.json').read_text()) for state in states]
                self.assertNotEqual(configs[0]['socket'], configs[1]['socket'])
                def private(config, *args):
                    return subprocess.check_output([str(wrapper), '-S', config['socket'], *args], env=env, text=True).strip()
                for config, sid in zip(configs, ids):
                    panes = private(config, 'list-panes', '-t', config['window'], '-F', '#{pane_id} #{pane_active}').splitlines()
                    self.assertEqual(len(panes), 1)
                    self.assertEqual(panes[0].split()[1], '1')
                    self.assertEqual(private(config, 'show-options', '-v', '-t', config['session'], 'status-position'), 'bottom')
                    self.assertEqual(private(config, 'show-options', '-v', '-t', config['session'], 'status-style'), 'fg=default,bg=default')
                    screen = private(config, 'display-message', '-p', '-t', config['main_pane'], '#{E:status-format[0]}')
                    self.assertIn('25%', screen)
                    self.assertIn(sid[:8], screen)
                sessions = subprocess.check_output([str(wrapper), 'list-sessions', '-F', '#{session_name}'], env=env, text=True).strip()
                self.assertEqual(sessions, 'keeper', 'Codex sessions leaked into the original server')
                for key, expected in [('status', '2'), ('status-position', 'top'), ('status-format[0]', 'original status'), ('status-format[1]', 'second line')]:
                    actual = subprocess.check_output([str(wrapper), 'show-options', '-v', '-t', 'keeper', key], env=env, text=True).strip()
                    self.assertEqual(actual, expected)
                private(configs[0], 'send-keys', '-t', configs[0]['main_pane'], 'C-c')
                deadline = time.monotonic() + 3
                while states[0].exists():
                    self.assertLess(time.monotonic(), deadline, 'Ctrl+C cleanup was not immediate')
                    time.sleep(.1)
                self.assertTrue(states[1].exists(), 'Other Codex server must survive')
                self.assertEqual(private(configs[1], 'list-sessions', '-F', '#{session_name}'), 'codex')
                (root / 'stop').touch()
                deadline = time.monotonic() + 3
                while states[1].exists():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.1)
                for config in configs:
                    result = subprocess.run([str(wrapper), '-S', config['socket'], 'list-sessions'], env=env, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                self.assertEqual(subprocess.run([str(wrapper), 'has-session', '-t', 'keeper'], env=env, capture_output=True).returncode, 0)
            finally:
                for state in states:
                    subprocess.run([str(wrapper), '-S', str(state / 'tmux.sock'), 'kill-server'], env=env, capture_output=True)
                subprocess.run([str(wrapper), 'kill-server'], env=env, capture_output=True)


if __name__ == '__main__':
    unittest.main()
