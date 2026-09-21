import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
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
                        CURL_ARGUMENTS=str(self.root / 'curl-arguments'), CODEX_TMUX_REF='main')
        self.prefix = self.root / 'install with spaces'
        self.rc = self.root / 'test.bashrc'

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
        self.assertEqual(self.rc.read_text(), '# keep\n')
        self.assertEqual(len(list(self.root.glob('test.bashrc.codex-tmux-*.bak'))), 1)
        for name in ('codex_tmux.py', 'usage.py'):
            self.assertEqual((self.prefix / 'share/codex-tmux' / name).read_bytes(),
                             (PROJECT / name).read_bytes())
        command = self.prefix / 'bin/codex-tmux'
        self.assertTrue(os.access(command, os.X_OK))
        self.assertIn(shlex.quote(str(self.prefix / 'share/codex-tmux/codex_tmux.py')),
                      command.read_text())
        self.assertIn('https://codeload.github.com/minsoft1115/codex-tmux/tar.gz/v1.2.3',
                      (self.root / 'curl-arguments').read_text())

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
