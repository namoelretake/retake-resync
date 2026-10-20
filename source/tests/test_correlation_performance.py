"""Check reuse without weakening the matcher's audio evidence or memory limits."""
import itertools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import correlate_features, correlation_session


def match_evidence(result):
    if result is None:
        return None
    return (*result, result.margin, result.runnerup)


class TestCorrelationReuse(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(220108)

    def assert_cached_matches(self, pairs):
        expected = [match_evidence(correlate_features(a, b)) for a, b in pairs]
        with correlation_session():
            actual = [match_evidence(correlate_features(a, b)) for a, b in pairs]
            repeated = [match_evidence(correlate_features(a, b)) for a, b in pairs]
        self.assertEqual(actual, expected)
        self.assertEqual(repeated, expected)

    def test_preserves_offset_score_and_ambiguity_exactly(self):
        source = self.rng.normal(size=(3000, 15)).astype(np.float32)
        excerpt = source[345:1745] + self.rng.normal(
            scale=.07, size=(1400, 15)).astype(np.float32)
        unrelated = self.rng.normal(size=(1900, 15)).astype(np.float32)
        repeated = np.tile(source[:150], (9, 1))
        silence = np.zeros((400, 15), dtype=np.float32)
        self.assert_cached_matches([
            (source, excerpt), (excerpt, source), (source, unrelated),
            (repeated, repeated.copy()), (silence, silence.copy()),
            (source[:99], excerpt), (source[::2], excerpt[::2]),
        ])
        self.assertEqual(correlate_features(source, excerpt)[0], 345 / 50)

    def test_one_forward_transform_per_recording_for_same_fft_size(self):
        sources = [self.rng.normal(size=(900 + i * 10, 15)).astype(np.float32)
                   for i in range(8)]
        pairs = list(itertools.combinations(sources, 2))
        with patch('engine.np.fft.rfft', wraps=np.fft.rfft) as transform:
            with correlation_session():
                for a, b in pairs:
                    correlate_features(a, b)
        # 28 comparisons used to require 56 forward FFTs; now each recording
        # needs one. Inverse FFTs still verify every pair with the same math.
        self.assertEqual(transform.call_count, len(sources))

    def test_same_recording_can_use_different_fft_sizes(self):
        source = self.rng.normal(size=(1600, 5)).astype(np.float32)
        short = source[150:650].copy()
        long = self.rng.normal(size=(5500, 5)).astype(np.float32)
        long[2000:3600] = source
        self.assert_cached_matches([(source, short), (source, long),
                                    (short, source), (long, source)])

    def test_cache_budget_never_changes_the_result(self):
        sources = [self.rng.normal(size=(800, 3)).astype(np.float32)
                   for _ in range(4)]
        pairs = list(itertools.combinations(sources, 2))
        expected = [match_evidence(correlate_features(a, b)) for a, b in pairs]
        # Each FFT exceeds this budget, and only two energy prefixes fit.
        with correlation_session(max_bytes=16000) as cache:
            for (a, b), evidence in zip(pairs, expected):
                self.assertEqual(match_evidence(correlate_features(a, b)), evidence)
                self.assertLessEqual(cache.bytes, 16000)
        self.assertEqual(cache.bytes, 0)
        self.assertFalse(cache.entries)

    def test_cache_is_released_after_exception_and_jobs_are_independent(self):
        a = self.rng.normal(size=(500, 5)).astype(np.float32)
        b = a[100:350].copy()
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            with correlation_session() as aborted_cache:
                correlate_features(a, b)
                raise RuntimeError('cancelled')
        self.assertFalse(aborted_cache.entries)
        with patch('engine.np.fft.rfft', wraps=np.fft.rfft) as transform:
            with correlation_session():
                correlate_features(a, b)
            with correlation_session():
                correlate_features(a, b)
        self.assertEqual(transform.call_count, 4)

    def test_nested_sessions_restore_outer_session(self):
        a = self.rng.normal(size=(500, 5)).astype(np.float32)
        b = a[100:350].copy()
        with patch('engine.np.fft.rfft', wraps=np.fft.rfft) as transform:
            with correlation_session():
                outer = match_evidence(correlate_features(a, b))
                with correlation_session():
                    self.assertEqual(match_evidence(correlate_features(a, b)), outer)
                self.assertEqual(match_evidence(correlate_features(a, b)), outer)
        self.assertEqual(transform.call_count, 4)

    def test_simultaneous_jobs_keep_separate_caches(self):
        a = self.rng.normal(size=(500, 5)).astype(np.float32)
        b = a[100:350].copy()
        barrier = threading.Barrier(2, timeout=5)

        def job():
            with correlation_session() as cache:
                barrier.wait()
                evidence = match_evidence(correlate_features(a, b))
                barrier.wait()
                self.assertGreater(cache.bytes, 0)
                return cache, evidence

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(job)
            second = pool.submit(job)
            cache_a, evidence_a = first.result()
            cache_b, evidence_b = second.result()
        self.assertIsNot(cache_a, cache_b)
        self.assertEqual(evidence_a, evidence_b)
        self.assertFalse(cache_a.entries)
        self.assertFalse(cache_b.entries)


if __name__ == '__main__':
    unittest.main()
