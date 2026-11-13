"""Offset refinement must improve verified blocks without inventing matches."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offset_fit import fit_offsets


class TestOffsetFit(unittest.TestCase):
    def assert_offsets(self, actual, expected, places=9):
        self.assertEqual(actual.keys(), expected.keys())
        for key, offset in expected.items():
            self.assertAlmostEqual(actual[key], offset, places=places, msg=key)

    def test_cycle_refines_small_errors_with_weighted_residuals(self):
        # The last edge closes a cycle with a 60 ms disagreement. Its lower
        # confidence means that the two other lags should move only 12 ms.
        original = {"A": 0.0, "B": 2.0, "C": 5.0}
        edges = [("A", "B", 2, .9), ("B", "C", 3, .9), ("A", "C", 5.06, .3)]
        refined = fit_offsets(original, edges)
        self.assert_offsets(refined, {"A": 0.0, "B": 2.012, "C": 5.024})
        self.assertEqual(original, {"A": 0.0, "B": 2.0, "C": 5.0})

    def test_dense_overlap_can_outvote_a_high_scoring_false_edge(self):
        original = {"A": 0.0, "B": 2.9, "C": 3.0, "D": 5.0}
        edges = [("A", "B", 2.9, 1.15), ("A", "C", 3, .8), ("A", "D", 5, .8),
                 ("B", "C", 1.8, .8), ("B", "D", 3.8, .8), ("C", "D", 2, .8)]
        fitted = fit_offsets(original, edges)
        self.assert_offsets(fitted, {"A": 0.0, "B": 1.2, "C": 3.0, "D": 5.0})
        # A different origin changes only the block's translation. Reverse
        # endpoint order as a check against an accidental lag sign flip.
        reverse = [(v, u, -lag, weight) for u, v, lag, weight in reversed(edges)]
        fitted = fit_offsets(original, reverse, anchor="B")
        self.assert_offsets(fitted, {"A": 1.7, "B": 2.9, "C": 4.7, "D": 6.7})

    def test_only_bridge_preserves_lag_when_its_block_moves(self):
        original = {"A": 0.0, "B": 2.0, "C": 5.0, "E": 20.0}
        edges = [("A", "B", 2, .9), ("B", "C", 3, .9), ("A", "C", 5.06, .3),
                 ("C", "E", 15.09, .9)]
        fitted = fit_offsets(original, edges)
        self.assert_offsets(fitted, {"A": 0, "B": 2.012, "C": 5.024, "E": 20.024})
        self.assertAlmostEqual(fitted["E"] - fitted["C"], 15)

    def test_tree_has_no_support_to_change_offsets(self):
        original = {0: 8.0, 1: 10.0, 2: 5.0, 3: 9.0}
        edges = [(0, 1, 2.01, .8), (1, 2, -4.98, .8), (2, 3, 4.03, .8)]
        self.assertEqual(fit_offsets(original, edges), original)

    def test_contradictory_triangle_is_ambiguous_even_with_duplicate_pair(self):
        original = {"A": 0.0, "B": 2.0, "C": 5.0}
        edges = [("A", "B", 2, .75), ("B", "C", 3, .8),
                 ("A", "C", 8, .99), ("C", "A", -8, .99)] * 4
        self.assertEqual(fit_offsets(original, edges), original)

    def test_contradictory_square_is_not_averaged_into_false_agreement(self):
        original = {"A": 0.0, "B": 1.0, "C": 2.0, "D": 3.0}
        edges = [("A", "B", 1, .9), ("B", "C", 1, .9), ("C", "D", 1, .9),
                 ("A", "D", 3.3, .9)]
        self.assertEqual(fit_offsets(original, edges), original)

    def test_moderate_triangle_conflict_does_not_confirm_its_own_lag(self):
        original = {"A": 0.0, "B": 2.0, "C": 5.0}
        edges = [("A", "B", 2, .9), ("B", "C", 3, .9), ("A", "C", 5.18, .9)]
        self.assertEqual(fit_offsets(original, edges), original)

    def test_disagreeing_duplicate_pair_is_not_treated_as_two_votes(self):
        original = {"A": 0.0, "B": 2.0, "C": 5.0}
        edges = [("A", "B", 2, .9), ("B", "C", 3, .9),
                 ("A", "C", 5.06, .9), ("C", "A", -6, .9)]
        self.assertEqual(fit_offsets(original, edges), original)

    def test_unknown_nonfinite_or_disconnected_evidence_is_not_promoted(self):
        original = {"A": 0.0, "B": 2.0, "C": 5.0, "D": 12.0}
        edges = [("A", "B", 2, .9), ("B", "C", 3, .9), ("A", "C", 5.06, .9),
                 ("C", "D", float("nan"), .9), ("C", "D", 7, float("inf")),
                 ("A", "D", 12, 0), ("D", "D", 0, .9), ("C", "E", 9, .9)]
        self.assertEqual(fit_offsets(original, edges), original)


if __name__ == "__main__":
    unittest.main()
