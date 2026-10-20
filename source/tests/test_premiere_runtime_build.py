"""A failed runtime candidate must never destroy an existing staging build."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPOSITORY = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('premiere_runtime_builder', REPOSITORY / 'build/build_premiere_runtime.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class TestPremiereRuntimeBuild(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stage = self.root / 'plugin-stage'
        self.runtime = self.stage / 'mac/arm64/runtime'
        self.runtime.mkdir(parents=True)
        (self.runtime / 'runtime.json').write_text(json.dumps({
            'schema_version': 1,
            'worker_relative_path': 'resync-premiere-worker/resync-premiere-worker',
        }))
        self.old = self.runtime / 'previous-good-build'
        self.old.write_bytes(b'working runtime must survive')

    def test_failed_publish_restores_previous_runtime(self):
        scratch = self.root / 'scratch'
        scratch.mkdir()
        with self.assertRaises(OSError):
            builder.publish_runtime(self.root / 'missing-candidate', self.runtime, True, scratch)
        self.assertEqual(self.old.read_bytes(), b'working runtime must survive')
        self.assertTrue((self.runtime / 'runtime.json').is_file())
        self.assertFalse((scratch / 'previous-runtime').exists())

    def test_replace_refuses_unrecognized_directory(self):
        (self.runtime / 'runtime.json').unlink()
        candidate = self.root / 'candidate'
        candidate.mkdir()
        with self.assertRaises(ValueError):
            builder.publish_runtime(candidate, self.runtime, True, self.root)
        self.assertTrue(candidate.exists())
        self.assertEqual(self.old.read_bytes(), b'working runtime must survive')

    def test_failed_smoke_cannot_replace_previous_good_runtime(self):
        ffmpeg = self.root / 'ffmpeg'
        ffmpeg.write_bytes(b'build dependency')
        info = {'platform': 'darwin', 'architecture': 'arm64', 'python_version': '3.12',
                'numpy_version': 'test', 'pyinstaller_version': 'test',
                'imageio_ffmpeg_version': 'test', 'ffmpeg': str(ffmpeg), 'license_files': []}

        def execute(command, **_kwargs):
            if command[-1] == '-version':
                return subprocess.CompletedProcess(command, 0, 'ffmpeg version test\n', '')
            if command[-1] == '-L':
                return subprocess.CompletedProcess(command, 0, 'dependency notice\n', '')
            if 'PyInstaller' in command:
                dist = Path(command[command.index('--distpath') + 1]) / builder.WORKER_NAME
                dist.mkdir(parents=True)
                (dist / builder.WORKER_NAME).write_bytes(b'candidate executable')
                return subprocess.CompletedProcess(command, 0)
            if Path(command[1]).name == 'test_premiere_runtime.py':
                raise subprocess.CalledProcessError(1, command)
            self.fail(f'Unexpected command: {command}')

        with (patch.object(builder, 'dependency_info', return_value=info),
              patch.object(builder.subprocess, 'run', side_effect=execute)):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build_runtime(self.stage, Path(sys.executable), 'test', replace=True, smoke=True)
        self.assertEqual(self.old.read_bytes(), b'working runtime must survive')
        self.assertTrue((self.runtime / 'runtime.json').is_file())


if __name__ == '__main__':
    unittest.main()
