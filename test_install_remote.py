import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import tomllib
import unittest


PROJECT = Path(__file__).resolve().parent


class RemoteInstallTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.downloads = self.root / 'downloads'
        self.downloads.mkdir()
        self.archive = self.root / 'fixture.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name in ('install.sh', 'codex_tmux.py', 'usage.py'):
                archive.add(PROJECT / name, arcname=f'codex-tmux-fixture/{name}')
        curl = self.bin / 'curl'
        curl.write_text('''#!/bin/bash
set -eu
printf '%s\\n' "$@" > "$CURL_ARGUMENTS"
if [[ ${FAIL_DOWNLOAD:-0} == 1 ]]; then exit 22; fi
while [[ $# -gt 0 ]]; do
    if [[ $1 == --output ]]; then cp "$FIXTURE_ARCHIVE" "$2"; exit; fi
    shift
done
exit 2
''')
        curl.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}',
                        TMPDIR=str(self.downloads), FIXTURE_ARCHIVE=str(self.archive),
                        CURL_ARGUMENTS=str(self.root / 'curl-arguments'), CODEX_TMUX_REF='main',
                        CODEX_HOME=str(self.root / 'codex-home'))
        self.prefix = self.root / 'install with spaces'
        self.rc = self.root / 'test.bashrc'
        self.config = Path(self.env['CODEX_HOME']) / 'config.toml'

    def install(self, *extra):
        # Feed the script through stdin exactly as curl | bash would.
        result = subprocess.run(
            ['bash', '-s', '--', '--prefix', str(self.prefix), '--bashrc', str(self.rc), *extra],
            input=(PROJECT / 'install-remote.sh').read_text(),
            text=True, capture_output=True, env=self.env, cwd=self.root)
        self.assertEqual(list(self.downloads.iterdir()), [], result.stderr)
        return result

    def test_install_options_migration_and_reinstall(self):
        self.env['CODEX_TMUX_REF'] = 'v1.2.3'
        self.rc.write_text("# keep\n# >>> codex-tmux >>>\nalias codex-tmux='old'\n# <<< codex-tmux <<<\n")
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.rc.read_text().startswith('# keep\n'))
        self.assertEqual(self.rc.read_text().count('alias codex='), 1)
        self.assertNotIn("alias codex-tmux=", self.rc.read_text())
        self.assertEqual(len(list(self.root.glob('test.bashrc.codex-tmux-*.bak'))), 1)
        for name in ('codex_tmux.py', 'usage.py', 'install.sh'):
            self.assertEqual((self.prefix / 'share/codex-tmux' / name).read_bytes(),
                             (PROJECT / name).read_bytes())
        command = self.prefix / 'bin/codex-tmux'
        self.assertTrue(os.access(command, os.X_OK))
        self.assertIn(shlex.quote(str(self.prefix / 'share/codex-tmux/codex_tmux.py')),
                      command.read_text())
        self.assertIn('https://codeload.github.com/minsoft1115/codex-tmux/tar.gz/v1.2.3',
                      (self.root / 'curl-arguments').read_text())

    def uninstall(self):
        return subprocess.run(
            ['bash', str(PROJECT / 'install.sh'), '--uninstall', '--prefix', str(self.prefix), '--bashrc', str(self.rc)],
            text=True, capture_output=True, env=self.env)

    def test_newline_keymap_preserves_settings_and_reinstall_is_idempotent(self):
        originals = [
            '# keep\nmodel = "example"\n',
            '[tui.keymap.editor]\n# keep\nmove_left = "ctrl-b"\n',
            '[tui.keymap.editor]\ninsert_newline = [\n "alt-enter",\n "ctrl-j",\n]\nmove_left = "ctrl-b"\n',
            'tui.keymap.editor.insert_newline = "alt-enter"\n# keep\nmodel = "example"\n',
            'note = """\n[tui.keymap.editor]\ninsert_newline = "fake"\n"""\n',
        ]
        self.config.parent.mkdir()
        for original in originals:
            with self.subTest(original=original):
                self.config.write_text(original)
                result = self.install()
                self.assertEqual(result.returncode, 0, result.stderr)
                installed = self.config.read_bytes()
                parsed = tomllib.loads(installed.decode())
                bindings = parsed['tui']['keymap']['editor'].pop('insert_newline')
                self.assertEqual(bindings[0], 'shift-enter')
                self.assertNotIn('alt-enter', bindings)
                expected = tomllib.loads(original)
                editor = expected.setdefault('tui', {}).setdefault('keymap', {}).setdefault('editor', {})
                editor.pop('insert_newline', None)
                self.assertEqual(parsed, expected)
                backups = list(self.config.parent.glob('config.toml.codex-tmux-*.bak'))
                self.assertTrue(any(path.read_text() == original for path in backups))
                self.assertEqual(self.install().returncode, 0)
                self.assertEqual(self.config.read_bytes(), installed)
                self.assertEqual(list(self.config.parent.glob('config.toml.codex-tmux-*.bak')), backups)
        self.assertEqual(self.uninstall().returncode, 0)
        self.assertEqual(self.config.read_bytes(), installed)

    def test_invalid_codex_config_does_not_change_installation(self):
        self.config.parent.mkdir()
        original = '[tui\n'
        self.config.write_text(original)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no changes made', result.stderr)
        self.assertEqual(self.config.read_text(), original)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.rc.exists())

    def test_alias_forwards_to_real_codex_without_recursion(self):
        import json
        self.rc.write_text("# keep\nalias codex='old-codex'\n")
        fake = self.bin / 'codex'
        fake.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                        'print(json.dumps(sys.argv[1:]))\nsys.exit(17)\n')
        fake.chmod(0o755)
        self.assertEqual(self.install().returncode, 0)
        result = subprocess.run(
            ['bash', '--noprofile', '--norc', '-O', 'expand_aliases', '-c',
             'source "$1"\ncodex --version\n', 'test', str(self.rc)],
            env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['--version'])
        self.assertEqual(self.uninstall().returncode, 0)
        self.assertEqual(self.rc.read_text(), "# keep\nalias codex='old-codex'\n")

    def test_malformed_alias_block_does_not_change_installation(self):
        original = '# >>> codex-tmux >>>\nmissing_end\n'
        self.rc.write_text(original)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.rc.read_text(), original)
        self.assertFalse(self.prefix.exists())

    def test_installed_command_uninstalls_without_source_or_download(self):
        self.rc.write_text('# keep my shell settings\n')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        command = self.prefix / 'bin/codex-tmux'
        help_result = subprocess.run([str(command), '--tmux-help'], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        app = self.prefix / 'share/codex-tmux'
        cache = app / '__pycache__'
        cache.mkdir(exist_ok=True)
        (cache / 'usage.cpython-311.pyc').write_bytes(b'test cache')
        self.env['FAIL_DOWNLOAD'] = '1'
        result = subprocess.run([str(command), '--uninstall'], cwd=self.root,
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(command.exists())
        self.assertFalse(app.exists())
        self.assertEqual(self.rc.read_text(), '# keep my shell settings\n')
        self.assertTrue((PROJECT / 'codex_tmux.py').exists())
        self.assertEqual(self.uninstall().returncode, 0)

    def test_uninstall_preserves_unrelated_files(self):
        self.assertEqual(self.install().returncode, 0)
        app = self.prefix / 'share/codex-tmux'
        extra = app / 'notes.txt'
        extra.write_text('keep')
        result = self.uninstall()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(extra.read_text(), 'keep')
        self.assertFalse((app / 'codex_tmux.py').exists())

    def test_uninstall_refuses_unmanaged_command(self):
        self.assertEqual(self.install().returncode, 0)
        command = self.prefix / 'bin/codex-tmux'
        command.write_text('#!/bin/sh\necho custom\n')
        result = self.uninstall()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unmanaged command', result.stderr)
        self.assertEqual(command.read_text(), '#!/bin/sh\necho custom\n')
        self.assertTrue((self.prefix / 'share/codex-tmux/codex_tmux.py').exists())

    def test_uninstall_refuses_symlinked_application(self):
        self.assertEqual(self.install().returncode, 0)
        app = self.prefix / 'share/codex-tmux'
        target = self.root / 'other-installation'
        app.rename(target)
        app.symlink_to(target, target_is_directory=True)
        result = self.uninstall()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((target / 'codex_tmux.py').exists())
        self.assertTrue((self.prefix / 'bin/codex-tmux').exists())

    def test_failed_download_does_not_install(self):
        self.env['FAIL_DOWNLOAD'] = '1'
        result = self.install()
        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertFalse(self.prefix.exists())

    def test_invalid_archive_does_not_install(self):
        self.archive.write_text('not an archive')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.prefix.exists())

    def test_installer_failure_is_propagated(self):
        result = self.install('--unknown-option')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unknown option', result.stderr)
        self.assertFalse(self.prefix.exists())


if __name__ == '__main__':
    unittest.main()
