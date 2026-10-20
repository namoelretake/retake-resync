"""Compile and test the SDK-independent launcher; no Adobe headers are faked.

Run: python3 -m unittest discover -s premiere-uxp/native/tests -p 'test_*.py' -v
The generated launcher_driver can also smoke-test a real frozen runtime using
--native-directory /plugin/mac/arm64 --job /absolute/resync_<24hex>.job.json.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

NATIVE = Path(__file__).resolve().parents[1]
COMPILER = shutil.which('clang++') or shutil.which('c++')
JOB_NAME = 'resync_' + 'a' * 24 + '.job.json'


@unittest.skipUnless(COMPILER and os.name == 'posix', 'POSIX C++17 compiler required')
class TestRuntimeLauncher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='resync-launcher-build-')
        cls.addClassCleanup(cls.build.cleanup)
        root = Path(cls.build.name)
        cls.driver = root / 'launcher_driver'
        cls.worker = root / 'worker_probe'
        common = [COMPILER, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pthread']
        subprocess.run(common + ['-I', str(NATIVE), str(NATIVE / 'runtime_launcher.cpp'),
                                 str(NATIVE / 'tests' / 'launcher_driver.cpp'),
                                 '-o', str(cls.driver)], check=True, capture_output=True, text=True)
        subprocess.run(common + [str(NATIVE / 'tests' / 'worker_probe.cpp'),
                                 '-o', str(cls.worker)], check=True, capture_output=True, text=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='resync-launcher-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.native = self.root / 'plugin' / 'mac' / 'arm64'
        self.runtime = self.native / 'runtime'
        self.worker_path = self.runtime / 'resync-premiere-worker' / 'resync-premiere-worker'
        self.worker_path.parent.mkdir(parents=True)
        shutil.copy2(self.worker, self.worker_path)
        (self.runtime / 'bin').mkdir()
        self.ffmpeg = self.runtime / 'bin' / 'ffmpeg'
        shutil.copy2(self.worker, self.ffmpeg)
        (self.runtime / 'runtime.json').write_text('{"schema_version":1}')
        self.jobs = self.root / 'jobs'
        self.jobs.mkdir()
        self.job = self.jobs / JOB_NAME
        self.job.write_text('{}')

    def run_driver(self, *, job=None, abandon=False, native=None, environment=None):
        args = [str(self.driver), '--native-directory', str(native or self.native),
                '--job', str(job or self.job)]
        if abandon:
            args.append('--abandon')
        return subprocess.run(args, capture_output=True, text=True, timeout=10,
                              env=environment, cwd=self.root)

    def assert_rejected(self, **kwargs):
        result = self.run_driver(**kwargs)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('rejected request', result.stderr)
        self.assertFalse((self.jobs / 'probe.json').exists())

    def test_starts_fixed_worker_and_reaps_cached_exit_status(self):
        self.job.write_text('{"exit_code":7}')
        result = self.run_driver()
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual(status['exit_code'], 7)
        self.assertTrue(status['reaped'])
        probe = json.loads((self.jobs / 'probe.json').read_text())
        self.assertEqual(probe['argc'], 3)
        self.assertEqual(probe['argument'], '--job')
        self.assertEqual(probe['job'], str(self.job.resolve()))
        self.assertEqual(probe['pgroup'], status['pid'])
        self.assertTrue(probe['redirected'])

    def test_paths_are_literal_arguments_and_never_a_shell_command(self):
        # No shell interpolates quotes, spaces, Unicode, semicolons or $().
        odd = self.root / "ReSync ação ' $(touch LAUNCHER_INJECTION) ; plugin"
        self.native.rename(odd)
        odd_jobs = self.root / "jobs ' $(touch JOB_INJECTION) ; ação"
        self.jobs.rename(odd_jobs)
        job = odd_jobs / JOB_NAME
        result = self.run_driver(native=odd, job=job)
        self.assertEqual(result.returncode, 0, result.stderr)
        probe = json.loads((odd_jobs / 'probe.json').read_text())
        self.assertEqual(probe['job'], str(job.resolve()))
        self.assertFalse((self.root / 'LAUNCHER_INJECTION').exists())
        self.assertFalse((self.root / 'JOB_INJECTION').exists())

    def test_environment_is_clean_and_decoder_path_is_fixed(self):
        environment = dict(os.environ, PYTHONPATH='/malicious/modules',
                           PYTHONHOME='/malicious/python', DYLD_LIBRARY_PATH='/malicious/dylibs',
                           RESYNC_TEST_HOST_SECRET='do-not-inherit', PATH='/malicious/bin')
        result = self.run_driver(environment=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        probe = json.loads((self.jobs / 'probe.json').read_text())
        self.assertEqual(probe['path'], str((self.runtime / 'bin').resolve()) + ':/usr/bin:/bin')
        for key in ('pythonpath', 'pythonhome', 'dyld', 'custom_host_secret'):
            self.assertEqual(probe[key], '')

    def test_ignores_executable_paths_in_job_and_manifest(self):
        (self.runtime / 'runtime.json').write_text('{"worker_relative_path":"/bin/false"}')
        self.job.write_text('{"executable":"/bin/false","args":["-c","exit 99"]}')
        result = self.run_driver()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['exit_code'], 0)
        self.assertTrue((self.jobs / 'probe.json').exists())

    def test_invalid_job_names_relative_paths_and_nonfiles_are_rejected(self):
        for name in ('job.json', 'resync_' + 'A' * 24 + '.job.json',
                     'resync_' + 'a' * 23 + '.job.json',
                     'resync_' + 'a' * 24 + '.job.json;anything'):
            with self.subTest(name=name):
                path = self.jobs / name
                path.write_text('{}')
                self.assert_rejected(job=path)
        self.assert_rejected(job=Path('relative') / JOB_NAME)
        self.job.unlink()
        self.assert_rejected()
        self.job.mkdir()
        self.assert_rejected()

    def test_large_job_and_symlink_job_are_rejected(self):
        self.job.write_bytes(b'x' * 16385)
        self.assert_rejected()
        self.job.unlink()
        target = self.jobs / 'target.json'
        target.write_text('{}')
        self.job.symlink_to(target)
        self.assert_rejected()

    def test_embedded_nul_is_rejected_before_any_spawn(self):
        result = subprocess.run([str(self.driver), '--native-directory', str(self.native),
                                 '--job', str(self.job), '--embedded-nul'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('NUL', result.stderr)
        self.assertFalse((self.jobs / 'probe.json').exists())

    def test_missing_or_nonexecutable_worker_and_decoder_are_rejected(self):
        for target in (self.worker_path, self.ffmpeg):
            with self.subTest(target=target):
                original_mode = target.stat().st_mode
                target.chmod(0o600)
                self.assert_rejected()
                target.chmod(original_mode)
                backup = target.with_suffix('.backup')
                target.rename(backup)
                self.assert_rejected()
                backup.rename(target)

    def test_symlink_runtime_or_worker_cannot_escape_the_bundle(self):
        backup = self.runtime.with_name('outside-runtime')
        self.runtime.rename(backup)
        self.runtime.symlink_to(backup, target_is_directory=True)
        self.assert_rejected()
        self.runtime.unlink()
        backup.rename(self.runtime)
        self.worker_path.unlink()
        self.worker_path.symlink_to(self.worker)
        self.assert_rejected()

    def test_manifest_must_be_a_bounded_regular_file(self):
        manifest = self.runtime / 'runtime.json'
        for payload in ('', 'x' * 65537):
            with self.subTest(length=len(payload)):
                manifest.write_text(payload)
                self.assert_rejected()
        manifest.unlink()
        self.assert_rejected()

    def test_start_returns_quickly_and_handle_cleanup_kills_its_process_group(self):
        self.job.write_text('{"slow":true,"spawn_child":true}')
        started = time.monotonic()
        result = self.run_driver(abandon=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 5)
        status = json.loads(result.stdout)
        self.assertLess(status['spawn_ms'], 2000)
        self.assertTrue(status['reaped'])
        probe = json.loads((self.jobs / 'probe.json').read_text())
        self.assertEqual(probe['pgroup'], status['pid'])
        self.assertGreater(probe['child'], 0)
        # The owned worker was synchronously reaped. Its descendant belongs to
        # the OS after group termination; accept an OS-pending zombie as dead,
        # which matters in Linux CI containers whose PID 1 may reap slowly.
        deadline = time.monotonic() + 2
        state = 'running'
        while time.monotonic() < deadline:
            try:
                os.kill(probe['child'], 0)
                proc_stat = Path('/proc') / str(probe['child']) / 'stat'
                # Linux CI can expose an exited orphan before PID 1 reaps it.
                if proc_stat.exists():
                    state = proc_stat.read_text().split(') ', 1)[1].split()[0]
            except ProcessLookupError:
                state = ''
            if not state or state.startswith('Z'):
                break
            time.sleep(.01)
        self.assertTrue(not state or state.startswith('Z'), state)
        with self.assertRaises(ProcessLookupError):
            os.kill(status['pid'], 0)


if __name__ == '__main__':
    unittest.main()
