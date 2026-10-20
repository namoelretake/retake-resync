"""The plugin worker uses a fixed bundled runtime and a narrow CLI contract."""
import contextlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import premiere_worker


class TestPremiereWorker(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ffmpeg = self.root / 'bin' / 'ffmpeg'
        self.ffmpeg.parent.mkdir()
        self.ffmpeg.write_bytes(b'fake executable for contract tests')
        self.ffmpeg.chmod(0o755)
        self.metadata = {'schema_version': 1, 'worker_cli_protocol': 1, 'job_protocol': 1,
                         'engine_version': '0.6.11', 'platform': sys.platform,
                         'architecture': platform.machine(),
                         'worker_relative_path': premiere_worker.WORKER_RELATIVE_PATH,
                         'ffmpeg_relative_path': premiere_worker.FFMPEG_RELATIVE_PATH}
        self.write_metadata()
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)

    def write_metadata(self):
        (self.root / 'runtime.json').write_text(json.dumps(self.metadata), encoding='utf-8')

    def test_job_passes_as_one_argument_and_ignores_ambient_ffmpeg(self):
        job = self.root / 'pasta com espaços e acentos' / 'resync_0123456789abcdef01234567.job.json'
        os.environ['PATH'] = '/ambient/ffmpeg'
        os.environ['IMAGEIO_FFMPEG_EXE'] = '/ambient/other-ffmpeg'
        with patch('premiere_worker.run_job', return_value=0) as runner:
            self.assertEqual(premiere_worker.main(['--job', str(job)], root=self.root), 0)
        runner.assert_called_once_with(job)
        self.assertEqual(os.environ['PATH'], str(self.ffmpeg.parent.resolve()))

    def test_version_output_is_json_with_protocols_and_engine_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(premiere_worker.main(['--version-json'], root=self.root), 0)
        info = json.loads(output.getvalue())
        self.assertEqual(info['engine_version'], '0.6.11')
        self.assertEqual(info['job_protocol'], 1)
        self.assertEqual(info['worker_cli_protocol'], 1)
        self.assertEqual(info['architecture'], platform.machine())

    def test_missing_bundled_ffmpeg_cannot_fall_back_to_ambient_binary(self):
        self.ffmpeg.unlink()
        os.environ['PATH'] = '/opt/homebrew/bin:/usr/bin'
        with (patch('premiere_worker.run_job') as runner,
              contextlib.redirect_stderr(io.StringIO())):
            self.assertEqual(premiere_worker.main(['--job', str(self.root / 'job.json')], root=self.root), 2)
        runner.assert_not_called()

    def test_incompatible_metadata_cannot_launch_job(self):
        for key, value in [('job_protocol', 99), ('architecture', 'wrong-cpu'),
                           ('worker_relative_path', '/arbitrary/executable')]:
            with self.subTest(key=key):
                original = self.metadata[key]
                self.metadata[key] = value
                self.write_metadata()
                with (patch('premiere_worker.run_job') as runner,
                      contextlib.redirect_stderr(io.StringIO())):
                    self.assertEqual(premiere_worker.main(['--job', str(self.root / 'job.json')], root=self.root), 2)
                runner.assert_not_called()
                self.metadata[key] = original

    def test_relative_job_and_symlinked_ffmpeg_are_rejected(self):
        with (patch('premiere_worker.run_job') as runner,
              contextlib.redirect_stderr(io.StringIO())):
            self.assertEqual(premiere_worker.main(['--job', 'relative.job.json'], root=self.root), 2)
        runner.assert_not_called()
        target = self.root / 'external-ffmpeg'
        self.ffmpeg.rename(target)
        self.ffmpeg.symlink_to(target)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(premiere_worker.main(['--version-json'], root=self.root), 2)

    def test_worker_preserves_job_failure_exit_status(self):
        with patch('premiere_worker.run_job', return_value=1):
            self.assertEqual(premiere_worker.main(['--job', str(self.root / 'job.json')], root=self.root), 1)


if __name__ == '__main__':
    unittest.main()
