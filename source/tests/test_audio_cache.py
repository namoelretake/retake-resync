"""Persistent signatures must be identical, disposable and source-aware."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import AUDIO_RATE, MUSIC_IDENTITY_BINS, AudioCache


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for extraction checks')
class TestAudioCache(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'signatures'
        self.source = self.root / 'source.wav'
        self.duration = 6
        self.write_source(81)
        env = patch.dict(os.environ, {'RESYNC_DISABLE_CACHE': '0'})
        env.start()
        self.addCleanup(env.stop)

    def write_source(self, seed):
        rng = np.random.default_rng(seed)
        samples = rng.normal(0, .2, AUDIO_RATE * self.duration)
        samples *= .5 + .5 * np.sin(np.arange(len(samples)) / AUDIO_RATE * 7) ** 2
        with wave.open(str(self.source), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(AUDIO_RATE)
            stream.writeframes((samples * 32767).astype('<i2').tobytes())

    def cache(self, **kwargs):
        return AudioCache(cache_dir=self.directory, **kwargs)

    def features(self, cache):
        result = cache.features(str(self.source), 0, self.duration)
        self.assertIsNotNone(result)
        return result

    def write_checked_array(self, path, array):
        with path.open('wb') as stream:
            np.save(stream, array, allow_pickle=False)
            stream.write(hashlib.sha256(memoryview(array).cast('B')).digest())

    def test_new_instance_reuses_identical_features_without_ffmpeg(self):
        first_cache = self.cache()
        first = self.features(first_cache)
        first_identity = first_cache.verification_features(first)
        self.assertIsNotNone(first_identity)
        self.assertEqual(len(list(self.directory.glob('*.npy'))), 1)
        second_cache = self.cache()
        with patch('engine.subprocess.Popen', side_effect=AssertionError('cache should avoid FFmpeg')):
            second = self.features(second_cache)
        second_identity = second_cache.verification_features(second)
        self.assertIsNotNone(second_identity)
        np.testing.assert_array_equal(first, second)
        np.testing.assert_array_equal(first_identity, second_identity)
        self.assertEqual(second.dtype, np.float32)
        self.assertEqual(second.shape[1], 15)
        self.assertEqual(second_identity.shape, (len(second), MUSIC_IDENTITY_BINS))
        self.assertEqual(second_identity.dtype, np.float32)
        self.assertTrue(second.flags.c_contiguous)
        self.assertTrue(second_identity.flags.c_contiguous)
        combined = np.load(next(self.directory.glob('*.npy')), allow_pickle=False)
        self.assertEqual(combined.shape, (len(second), 15 + MUSIC_IDENTITY_BINS))
        np.testing.assert_array_equal(second, combined[:, :15])
        np.testing.assert_array_equal(second_identity, combined[:, 15:])
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_cache_does_not_change_extracted_signatures(self):
        disabled_cache = AudioCache(cache_dir=False)
        disk_cache = self.cache()
        original = self.features(disabled_cache)
        persisted = self.features(disk_cache)
        np.testing.assert_array_equal(original, persisted)
        np.testing.assert_array_equal(disabled_cache.verification_features(original),
                                      disk_cache.verification_features(persisted))

    def test_identity_reuses_the_existing_fft_and_is_available_from_memory(self):
        cache = self.cache()
        with (patch('engine.np.fft.rfft', wraps=np.fft.rfft) as transform,
              patch('engine.subprocess.Popen', wraps=subprocess.Popen) as decode):
            features = self.features(cache)
        self.assertEqual(decode.call_count, 1)
        self.assertEqual(transform.call_count, 1)
        fingerprint = cache.verification_features(features)
        self.assertIsNotNone(fingerprint)
        self.assertTrue(np.isfinite(fingerprint).all())
        self.assertTrue((fingerprint >= 0).all())
        np.testing.assert_allclose(np.linalg.norm(fingerprint, axis=1), 1, atol=1e-5)
        with patch('engine.subprocess.Popen', side_effect=AssertionError('memory hit should avoid FFmpeg')):
            self.assertIs(self.features(cache), features)
            self.assertIs(cache.verification_features(features), fingerprint)
        self.assertIsNone(cache.verification_features(np.zeros_like(features)))
        self.assertIsNone(cache.verification_features(None))

    def test_changed_content_invalidates_memory_and_disk_even_with_preserved_mtime(self):
        cache = self.cache()
        original = self.features(cache)
        before = self.source.stat()
        self.write_source(99)
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.source.stat().st_size, before.st_size)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)
        current = self.features(cache)
        self.assertFalse(np.array_equal(original, current))
        with patch('engine.subprocess.Popen', side_effect=AssertionError('new content should be cached')):
            reloaded = self.features(self.cache())
        np.testing.assert_array_equal(current, reloaded)

    def test_different_seek_or_duration_never_reuses_other_segment(self):
        cache = self.cache()
        self.features(cache)
        shifted = cache.features(str(self.source), .5, self.duration - .5)
        shorter = cache.features(str(self.source), 0, self.duration - 1)
        self.assertIsNotNone(shifted)
        self.assertIsNotNone(shorter)
        self.assertEqual(len(list(self.directory.glob('*.npy'))), 3)

    def test_missing_file_can_appear_during_same_sync(self):
        self.source.unlink()
        cache = self.cache()
        self.assertIsNone(cache.features(str(self.source), 0, self.duration))
        self.write_source(81)
        self.features(cache)

    def test_invalid_source_path_is_unavailable_instead_of_crashing(self):
        self.assertIsNone(self.cache().features('bad\x00path.wav', 0, self.duration))

    def test_decoder_replacement_invalidates_persistent_signatures(self):
        decoder = self.root / 'decoder'
        decoder.write_bytes(b'version 1')
        with patch('engine.shutil.which', return_value=str(decoder)):
            expected = self.features(self.cache())
            decoder.write_bytes(b'version 2 updated')
            with patch('engine.subprocess.Popen', wraps=subprocess.Popen) as decode:
                recovered = self.features(self.cache())
        self.assertEqual(decode.call_count, 1)
        self.assertEqual(len(list(self.directory.glob('*.npy'))), 2)
        np.testing.assert_array_equal(expected, recovered)

    def test_missing_decoder_does_not_break_cache_construction(self):
        with patch('engine.shutil.which', return_value=None):
            cache = self.cache()
        with patch('engine.subprocess.Popen', side_effect=FileNotFoundError('ffmpeg')):
            with self.assertRaises(FileNotFoundError):
                cache.features(str(self.source), 0, self.duration)

    def test_corrupted_and_nonfinite_cache_recover_by_decoding(self):
        expected = self.features(self.cache())
        path = next(self.directory.glob('*.npy'))
        for content in (b'incomplete cache', None):
            if content is None:
                corrupt = np.load(path, allow_pickle=False)
                corrupt[0, 0] = np.nan
                self.write_checked_array(path, corrupt)
            else:
                path.write_bytes(content)
            with patch('engine.subprocess.Popen', wraps=subprocess.Popen) as decode:
                recovered = self.features(self.cache())
            self.assertEqual(decode.call_count, 1)
            np.testing.assert_array_equal(expected, recovered)

    def test_finite_bit_flip_is_rejected_and_redecoded_exactly(self):
        expected = self.features(self.cache())
        path = next(self.directory.glob('*.npy'))
        with path.open('r+b') as stream:
            self.assertEqual(np.lib.format.read_magic(stream), (1, 0))
            np.lib.format.read_array_header_1_0(stream)
            payload_offset = stream.tell()
            first = stream.read(4)
            stream.seek(payload_offset)
            stream.write(bytes([first[0] ^ 1]) + first[1:])
        changed = np.load(path, allow_pickle=False)
        self.assertTrue(np.isfinite(changed).all())
        self.assertFalse(np.array_equal(expected, changed[:, :15]))
        with patch('engine.subprocess.Popen', wraps=subprocess.Popen) as decode:
            recovered = self.features(self.cache())
        self.assertEqual(decode.call_count, 1)
        np.testing.assert_array_equal(expected, recovered)
        with patch('engine.subprocess.Popen', side_effect=AssertionError('repaired cache should be reusable')):
            np.testing.assert_array_equal(expected, self.features(self.cache()))

    def test_finite_identity_bit_flip_also_invalidates_the_whole_cache(self):
        cache = self.cache()
        expected = self.features(cache)
        identity = cache.verification_features(expected)
        path = next(self.directory.glob('*.npy'))
        with path.open('r+b') as stream:
            np.lib.format.read_magic(stream)
            np.lib.format.read_array_header_1_0(stream)
            offset = stream.tell() + 15 * 4
            stream.seek(offset)
            first = stream.read(4)
            stream.seek(offset)
            stream.write(bytes([first[0] ^ 1]) + first[1:])
        corrupt = np.load(path, allow_pickle=False)
        self.assertTrue(np.isfinite(corrupt).all())
        np.testing.assert_array_equal(expected, corrupt[:, :15])
        self.assertFalse(np.array_equal(identity, corrupt[:, 15:]))
        repaired_cache = self.cache()
        with patch('engine.subprocess.Popen', wraps=subprocess.Popen) as decode:
            recovered = self.features(repaired_cache)
        self.assertEqual(decode.call_count, 1)
        np.testing.assert_array_equal(expected, recovered)
        np.testing.assert_array_equal(identity, repaired_cache.verification_features(recovered))

    def test_legacy_features_without_identity_are_redecoded(self):
        expected = self.features(self.cache())
        path = next(self.directory.glob('*.npy'))
        self.write_checked_array(path, expected)
        cache = self.cache()
        with (patch('engine.np.load', side_effect=AssertionError('wrong width rejected before load')),
              patch('engine.subprocess.Popen', wraps=subprocess.Popen) as decode):
            recovered = self.features(cache)
        self.assertEqual(decode.call_count, 1)
        np.testing.assert_array_equal(expected, recovered)
        self.assertIsNotNone(cache.verification_features(recovered))

    def test_huge_or_object_header_is_rejected_before_numpy_load(self):
        self.features(self.cache())
        path = next(self.directory.glob('*.npy'))
        invalid_headers = [
            {'descr': '<f4', 'fortran_order': False, 'shape': (2 ** 40, 15 + MUSIC_IDENTITY_BINS)},
            {'descr': '|O', 'fortran_order': False, 'shape': (100, 15 + MUSIC_IDENTITY_BINS)},
        ]
        for header in invalid_headers:
            with path.open('wb') as stream:
                np.lib.format.write_array_header_1_0(stream, header)
            with patch('engine.np.load', side_effect=AssertionError('unsafe array must not be loaded')):
                self.features(self.cache())

    def test_cache_errors_cannot_prevent_sync_or_leave_temporary_files(self):
        occupied = self.root / 'not-a-directory'
        occupied.write_text('unrelated file')
        self.features(AudioCache(cache_dir=occupied))
        with patch('engine.os.replace', side_effect=OSError('full disk')):
            self.features(self.cache())
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(occupied.read_text(), 'unrelated file')

    def test_source_modified_while_decoding_is_not_persisted(self):
        original_popen = subprocess.Popen

        def start_and_touch(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            st = self.source.stat()
            os.utime(self.source, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))
            return process

        with patch('engine.subprocess.Popen', side_effect=start_and_touch):
            self.features(self.cache())
        self.assertFalse(self.directory.exists())

    def test_environment_directory_and_disable_switch(self):
        with patch.dict(os.environ, {'RESYNC_CACHE_DIR': str(self.directory)}):
            expected = self.features(AudioCache())
        with patch.dict(os.environ, {'RESYNC_DISABLE_CACHE': '1'}):
            with patch('engine.subprocess.Popen', wraps=subprocess.Popen) as decode:
                fresh = self.features(AudioCache(cache_dir=self.directory))
            self.assertEqual(decode.call_count, 1)
        np.testing.assert_array_equal(expected, fresh)

    def test_oldest_signatures_are_evicted_within_budget(self):
        cache = self.cache()
        self.directory.mkdir()
        for index in range(4):
            path = self.directory / (f'{index:064x}' + '.npy')
            self.write_checked_array(path, np.ones((200, 15 + MUSIC_IDENTITY_BINS), dtype=np.float32))
            os.utime(path, (index + 1, index + 1))
        unrelated = self.directory / 'notes.txt'
        unrelated.write_text('leave this alone')
        with patch.object(cache, 'MAX_CACHE_BYTES', 45000):
            cache._trim_disk_cache()
        signatures = list(self.directory.glob('*.npy'))
        self.assertEqual(len(signatures), 2)
        self.assertLessEqual(sum(path.stat().st_size for path in signatures), 45000)
        self.assertTrue((self.directory / (f'{3:064x}' + '.npy')).exists())
        self.assertEqual(unrelated.read_text(), 'leave this alone')

    def test_only_abandoned_resync_temporary_files_are_removed(self):
        cache = self.cache()
        self.directory.mkdir()
        old = time.time() - 25 * 60 * 60
        stale = self.directory / 'resync-abandoned.tmp'
        active = self.directory / 'resync-active.tmp'
        unrelated = self.directory / 'someone-else.tmp'
        for path in (stale, active, unrelated):
            path.write_bytes(b'unfinished write')
        os.utime(stale, (old, old))
        os.utime(unrelated, (old, old))
        outside = self.root / 'keep.tmp'
        outside.write_bytes(b'unrelated target')
        os.utime(outside, (old, old))
        link = self.directory / 'resync-link.tmp'
        link.symlink_to(outside)
        cache._trim_disk_cache()
        self.assertFalse(stale.exists())
        self.assertTrue(active.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_bytes(), b'unrelated target')


if __name__ == '__main__':
    unittest.main()
