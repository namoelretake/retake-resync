"""Regression tests: independent synchronized scenes must never overlap in XML.

Each audio-verified connected component is a complete edit block. Linked
video/scratch audio may have slightly offset starts, so packing cannot rely on
only the group's anchor start or a nominal duration.
"""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Project, sync

FPS = 25
GAP = 10.0


def make_project(specs):
    """Create two Premiere tracks with independent scenes in edit order.

    Each spec: name, video-track, original position (s), duration (s),
    corresponding scratch-audio offset in frames.
    """
    root = ET.Element("xmeml", version="5")
    seq = ET.SubElement(root, "sequence")
    rate = ET.SubElement(seq, "rate")
    ET.SubElement(rate, "timebase").text = str(FPS)
    media = ET.SubElement(seq, "media")
    video = ET.SubElement(media, "video")
    audio = ET.SubElement(media, "audio")
    vtracks = [ET.SubElement(video, "track") for _ in range(2)]
    atracks = [ET.SubElement(audio, "track") for _ in range(2)]
    for name, track, source_start, duration, audio_frames in specs:
        p = Path("/tmp/resync-block-test") / (name + ".wav")
        start_frame = source_start * FPS
        duration_frames = duration * FPS
        for kind, tracks, delta, label in (
            ("v", vtracks, 0, name),
            ("a", atracks, audio_frames, name + " SCRATCH"),
        ):
            item = ET.SubElement(tracks[track], "clipitem", id=f"{name}-{kind}")
            for tag, val in (
                ("name", label),
                ("start", start_frame + delta),
                ("end", start_frame + delta + duration_frames),
                ("in", 0),
                ("out", duration_frames),
            ):
                ET.SubElement(item, tag).text = str(val)
            file_element = ET.SubElement(item, "file", id="f" + name + kind)
            ET.SubElement(file_element, "pathurl").text = p.as_uri()
            file_rate = ET.SubElement(file_element, "rate")
            ET.SubElement(file_rate, "timebase").text = str(FPS)
            link = ET.SubElement(item, "link")
            ET.SubElement(link, "linkclipref").text = f"{name}-{'a' if kind == 'v' else 'v'}"
    return Project(ET.tostring(root, encoding="utf-8"))


def run_synthetic_graph(project, links):
    """Provide deterministic audio evidence without external media files."""
    marker = {name: i + 1 for i, name in enumerate(sorted(
        {Path(g.path).stem for g in project.groups}
    ))}
    inverse = {v: k for k, v in marker.items()}

    def features(_self, path, _source_in, _duration):
        return np.full((32, 3), marker[Path(path).stem], dtype=np.float32)

    def match(left, right):
        a = inverse[int(left[0, 0])]
        b = inverse[int(right[0, 0])]
        if (a, b) in links:
            return (links[a, b], 0.96)
        if (b, a) in links:
            return (-links[b, a], 0.96)
        return None

    with (patch("engine.shutil.which", return_value="/usr/bin/ffmpeg"),
          patch("engine.locate_media", return_value=None),
          patch("engine.AudioCache.features", features),
          patch("engine.correlate_features", match),
          patch("engine.confirm_audio_identity", return_value=True)):
        return sync(project, threshold=.60)


def bounds(project, names, read_export=False):
    if read_export:
        root = ET.fromstring(project.to_xml())
        items = [item for item in root.iter("clipitem")
                 if item.get("id", "").split("-")[0] in names]
        return (
            min(int(x.findtext("start")) / FPS for x in items),
            max(int(x.findtext("end")) / FPS for x in items),
        )
    clips = [c for c in project.clips if c.uid.split("-")[0] in names]
    return (min(c.new_start for c in clips),
            max(c.new_start + c.duration for c in clips))


class TestVerifiedSyncBlocks(unittest.TestCase):
    def test_independent_sync_blocks_have_ten_seconds_between_full_clip_bounds(self):
        project = make_project([
            ("A1", 0, 0, 20, 2),    # scratch audio extends 0.08s past video
            ("B1", 1, 0, 14, 0),
            ("A2", 0, 28, 16, 0),
            ("B2", 1, 28, 17, -2),  # scratch audio begins 0.08s before video
        ])
        result = run_synthetic_graph(project, {("A1", "B1"): 4.0,
                                                ("A2", "B2"): 5.0})
        self.assertEqual(result["verified_components"], 2)
        self.assertEqual(result["unmatched"], 0)
        self.assertTrue(all(g.status == "synced" for g in project.groups))
        first_start, first_end = bounds(project, {"A1", "B1"})
        second_start, second_end = bounds(project, {"A2", "B2"})
        self.assertAlmostEqual(second_start - first_end, GAP, places=5)
        self.assertGreater(second_end, second_start)
        assigned = {g.title: g.assigned for g in project.groups}
        self.assertAlmostEqual(assigned["B1"] - assigned["A1"], 4.0)
        self.assertAlmostEqual(assigned["B2"] - assigned["A2"], 5.0)
        for scene in ({"A1", "B1"}, {"A2", "B2"}):
            for name in scene:
                video = next(c for c in project.clips if c.uid == name + "-v")
                scratch = next(c for c in project.clips if c.uid == name + "-a")
                source_offset = (scratch.start - video.start)
                self.assertAlmostEqual(scratch.new_start - video.new_start, source_offset)
        # Both exported tracks and their links must keep the same spacing.
        x1, y1 = bounds(project, {"A1", "B1"}, read_export=True)
        x2, y2 = bounds(project, {"A2", "B2"}, read_export=True)
        self.assertGreaterEqual(x2 - y1, GAP - 1 / FPS)
        self.assertGreaterEqual(x1, 0)
        self.assertEqual(project.to_xml(), project.to_xml())
        restored = Project(project.to_xml())
        self.assertEqual(len(restored.clips), 8)

    def test_three_verified_components_pack_sequentially(self):
        project = make_project([
            ("A1", 0, 0, 13, 0), ("B1", 1, 0, 15, 0),
            ("A2", 0, 30, 11, 0), ("B2", 1, 30, 15, 0),
            ("A3", 0, 65, 14, 0), ("B3", 1, 65, 16, 0),
        ])
        result = run_synthetic_graph(project, {
            ("A1", "B1"): 2.0, ("A2", "B2"): 3.0, ("A3", "B3"): 1.0,
        })
        self.assertEqual(result["verified_components"], 3)
        spans = [bounds(project, {f"A{i}", f"B{i}"}) for i in (1, 2, 3)]
        self.assertAlmostEqual(spans[1][0] - spans[0][1], GAP)
        self.assertAlmostEqual(spans[2][0] - spans[1][1], GAP)
        self.assertTrue(all(start >= 0 and end > start for start, end in spans))


if __name__ == "__main__":
    unittest.main()

