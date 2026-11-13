"""Controls for the approximate, opt-in audio candidate index."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sparse_match import sparse_candidates


def varied_sound(rng, frames=1200):
    # Simulate the analysis arrays, with a distinctive event envelope plus
    # independent variation in each spectral band (50 analysis frames/sec).
    envelope = np.repeat(rng.normal(size=(frames + 3) // 4), 4)[:frames]
    return (envelope[:, None] * rng.uniform(.7, 1.3, (1, 15))
            + rng.normal(0, .15, (frames, 15))).astype(np.float32)


def separated_recordings():
    rng = np.random.default_rng(381)
    coarse, tonal, sources = [], [], []
    for block in range(20):
        original = varied_sound(rng)
        for offset in (0, 65, 123):
            camera = original[offset:offset + 997]
            camera = camera * rng.uniform(.55, 1.45, (1, 15)) + rng.normal(0, .08, camera.shape)
            coarse.append(camera.astype(np.float32))
            tonal.append(np.full((len(camera), 12), 1 / np.sqrt(12), dtype=np.float32))
            sources.append(block)
    return coarse, tonal, sources


class TestSparseMatch(unittest.TestCase):
    def test_sixty_takes_find_shifted_eq_changed_recordings(self):
        coarse, tonal, sources = separated_recordings()
        rng = np.random.default_rng(123)
        order = rng.permutation(len(coarse))
        coarse, tonal, sources = ([values[i] for i in order] for values in (coarse, tonal, sources))
        expected = {(i, j) for i in range(60) for j in range(i + 1, 60) if sources[i] == sources[j]}
        candidates = sparse_candidates(coarse, tonal)
        self.assertIsNotNone(candidates)
        self.assertTrue(expected.issubset(candidates), expected.difference(candidates))
        self.assertLess(len(candidates), 1770 // 10)
        self.assertEqual(candidates, sorted(set(candidates)))
        self.assertEqual(candidates, sparse_candidates(coarse, tonal))

    def test_identical_rhythm_different_dominant_pitches_are_distinct(self):
        coarse, tonal, _ = separated_recordings()
        rng = np.random.default_rng(311)
        shared_rhythm = varied_sound(rng, 997)
        for pitch in (2, 2, 5, 5):
            camera = shared_rhythm + rng.normal(0, .02, shared_rhythm.shape)
            identity = np.zeros((len(camera), 12), dtype=np.float32)
            identity[:, pitch] = 1
            coarse.append(camera.astype(np.float32))
            tonal.append(identity)
        result = sparse_candidates(coarse, tonal)
        self.assertIsNotNone(result)
        self.assertEqual([(i, j) for i, j in result if i >= 60], [(60, 61), (62, 63)])

    def test_missing_identity_and_long_reference_are_never_pruned(self):
        coarse, tonal, _ = separated_recordings()
        tonal[0] = None
        coarse.append(np.zeros((8500, 15), dtype=np.float32))
        tonal.append(np.zeros((8500, 12), dtype=np.float32))
        candidates = sparse_candidates(coarse, tonal)
        self.assertIsNotNone(candidates)
        self.assertTrue({(0, i) for i in range(1, 61)}.issubset(candidates))
        self.assertTrue({(i, 60) for i in range(60)}.issubset(candidates))

    def test_repetitive_and_undersized_jobs_fall_back(self):
        coarse, tonal, _ = separated_recordings()
        self.assertIsNone(sparse_candidates(coarse[:12], tonal[:12]))
        self.assertIsNone(sparse_candidates(coarse, [None] * 60))
        self.assertIsNone(sparse_candidates([coarse[0]] * 60, [tonal[0]] * 60))
        # No recordings are related: coverage cannot justify using the index.
        rng = np.random.default_rng(981)
        noise = [varied_sound(rng, 997) for _ in range(60)]
        self.assertIsNone(sparse_candidates(noise, tonal))


if __name__ == '__main__':
    unittest.main()
