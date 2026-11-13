"""Checks that the experimental index cannot accept a pair by itself."""

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from test_music_blocks_regression import project_for
from engine import CorrelationMatch, sync


class TestNextEngine(unittest.TestCase):
    def test_index_rejection_retries_unconfirmed_takes(self):
        project = project_for([("A", 1, 0, 20), ("B", 2, 0, 20), ("C", 3, 0, 20)])
        markers = {group.title: i for i, group in enumerate(project.groups)}
        index = {i: name for name, i in markers.items()}
        examined = []

        def features(_, path, start, duration):
            return np.full((120, 15), markers[Path(path).stem], np.float32)

        def correlate(a, b):
            names = index[int(a[0, 0])], index[int(b[0, 0])]
            examined.append(names)
            return CorrelationMatch(2, .94, .20, .74)

        def identity(_, a, b, lag):
            return {index[int(a[0, 0])], index[int(b[0, 0])]} == {"A", "C"}

        with (patch("engine.shutil.which", return_value="/usr/bin/ffmpeg"),
              patch("engine.locate_media"),
              patch("engine.AudioCache.features", features),
              patch("sparse_match.sparse_candidates", return_value=[(0, 1)]),
              patch("engine.correlate_features", correlate),
              patch("engine.confirm_audio_identity", identity)):
            result = sync(project, engine_mode="next")

        self.assertEqual(len(set(examined)), 3)
        self.assertEqual(result["matching"]["fallback_pairs"], 2)
        self.assertEqual(result["verified_components"], 1)
        self.assertEqual([row["name"] for row in result["groups"] if row["status"] == "unmatched"], ["B"])
