"""Controlled graph evidence for separated songs and genuine recording bridges.

These tests isolate graph decisions from audio matching. Genuine recording
bridges must survive without assuming camera order or requiring triangles.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import CorrelationMatch, Project, sync

FPS = 25


def project_for(takes):
    """Each take is (name, lane, original position, duration)."""
    root = ET.Element('xmeml', version='5')
    seq = ET.SubElement(root, 'sequence')
    rate = ET.SubElement(seq, 'rate')
    ET.SubElement(rate, 'timebase').text = str(FPS)
    media = ET.SubElement(seq, 'media')
    video = ET.SubElement(media, 'video')
    tracks = {lane: ET.SubElement(video, 'track') for lane in sorted({x[1] for x in takes})}
    for name, lane, start, duration in takes:
        item = ET.SubElement(tracks[lane], 'clipitem', id=name)
        for tag, value in [('name', name), ('start', start * FPS),
                           ('end', (start + duration) * FPS), ('in', 0),
                           ('out', duration * FPS)]:
            ET.SubElement(item, tag).text = str(value)
        source = ET.SubElement(item, 'file', id='file-' + name)
        ET.SubElement(source, 'pathurl').text = (Path('/tmp/resync-music-blocks') / (name + '.wav')).as_uri()
        source_rate = ET.SubElement(source, 'rate')
        ET.SubElement(source_rate, 'timebase').text = str(FPS)
    return Project(ET.tostring(root, encoding='utf-8'))


def with_evidence(project, edges):
    """Each edge is (left, right): (lag seconds, score, peak margin)."""
    markers = {g.title: index + 1 for index, g in enumerate(project.groups)}
    names = {value: name for name, value in markers.items()}

    def features(_self, path, _source_in, _duration):
        return np.full((120, 15), markers[Path(path).stem], dtype=np.float32)

    def correlate(left, right):
        a, b = names[int(left[0, 0])], names[int(right[0, 0])]
        reverse = (a, b) not in edges
        result = edges.get((b, a) if reverse else (a, b))
        if result is None:
            return None
        lag, score, margin = result
        return CorrelationMatch(-lag if reverse else lag, score, margin, score - margin)

    with (patch('engine.shutil.which', return_value='/usr/bin/ffmpeg'),
          patch('engine.locate_media', return_value=None),
          patch('engine.AudioCache.features', features),
          patch('engine.correlate_features', correlate),
          patch('engine.confirm_audio_identity', return_value=True)):
        return sync(project)


class TestMusicBlockEvidence(unittest.TestCase):
    def song_project(self):
        return project_for([
            ('A1', 1, 0, 20), ('A2', 1, 40, 20),
            ('B1', 2, 0, 20), ('B2', 2, 40, 20),
        ])

    def strong_pairs(self):
        return {('A1', 'B1'): (2, .96, .20), ('A2', 'B2'): (2, .95, .20)}

    def test_two_strong_pairs_without_bridge_remain_separate_blocks(self):
        project = self.song_project()
        result = with_evidence(project, self.strong_pairs())
        self.assertEqual(result['verified_components'], 2)
        self.assertEqual(result['unmatched'], 0)
        assigned = {g.title: g.assigned for g in project.groups}
        self.assertAlmostEqual(assigned['A2'] - (assigned['B1'] + 20), 10)

    def test_genuine_three_camera_bridge_requires_no_triangle_or_original_order(self):
        project = project_for([('A', 1, 70, 20), ('C', 1, 0, 20), ('B', 2, 0, 20)])
        result = with_evidence(project, {('A', 'B'): (12, .92, .20),
                                         ('B', 'C'): (12, .91, .20)})
        assigned = {g.title: g.assigned for g in project.groups}
        self.assertEqual(result['verified_components'], 1)
        self.assertEqual(result['unmatched'], 0)
        self.assertAlmostEqual(assigned['B'] - assigned['A'], 12)
        self.assertAlmostEqual(assigned['C'] - assigned['A'], 24)

    def test_continuous_reference_preserves_true_spacing_between_separate_takes(self):
        project = project_for([('A1', 1, 0, 20), ('A2', 1, 25, 20), ('REFERENCE', 2, 0, 120)])
        result = with_evidence(project, {('REFERENCE', 'A1'): (10, .96, .20),
                                         ('REFERENCE', 'A2'): (70, .95, .20)})
        assigned = {g.title: g.assigned for g in project.groups}
        self.assertEqual(result['verified_components'], 1)
        self.assertEqual(result['unmatched'], 0)
        self.assertAlmostEqual(assigned['A1'] - assigned['REFERENCE'], 10)
        self.assertAlmostEqual(assigned['A2'] - assigned['REFERENCE'], 70)
        self.assertAlmostEqual(assigned['A2'] - assigned['A1'], 60)


if __name__ == '__main__':
    unittest.main()
