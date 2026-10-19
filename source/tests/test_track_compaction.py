"""Regression tests: Premiere XML should reuse empty video/audio tracks globally.

The reference example had 29 video tracks, although only a few recordings
really overlapped. We must not change clip timecodes, audio routing or links.
"""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Project


FPS = 25


def xml_clip(uid, start, duration, link=None):
    link_xml = ''
    if link:
        ref, kind, track = link
        link_xml = (f'<link><linkclipref>{ref}</linkclipref>'
                    f'<mediatype>{kind}</mediatype><trackindex>{track}</trackindex>'
                    '<clipindex>1</clipindex></link>')
    return (f'<clipitem id="{uid}"><name>{uid}</name>'
            f'<start>{start}</start><end>{start + duration}</end>'
            f'<in>0</in><out>{duration}</out>'
            f'<file id="file-{uid}"><pathurl>file:///tmp/{uid}.mov</pathurl></file>'
            f'{link_xml}</clipitem>')


def make_xml(video_tracks, audio_tracks=()):
    def tracks(items):
        result = []
        for obj in items:
            metadata = obj.get('metadata', '<enabled>TRUE</enabled><locked>FALSE</locked>')
            result.append(f'<track>{metadata}{obj["body"]}</track>')
        return ''.join(result)
    return (
        '<xmeml version="5"><sequence><name>Test</name>'
        '<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'
        f'<media><video>{tracks(video_tracks)}</video>'
        f'<audio>{tracks(audio_tracks)}</audio></media></sequence></xmeml>'
    ).encode()


def assert_no_overlap(testcase, root, kind):
    for track in root.findall(f'./sequence/media/{kind}/track'):
        intervals = sorted((int(c.findtext('start')), int(c.findtext('end')))
                           for c in track.findall('clipitem'))
        for (_, prev_end), (start, _) in zip(intervals, intervals[1:]):
            testcase.assertGreaterEqual(start, prev_end, 'Two clips collided in one track')


class TestGlobalTrackCompaction(unittest.TestCase):
    def test_29_separate_video_audio_tracks_reuse_two_lanes(self):
        video = []
        audio = []
        expected = {}
        for i in range(29):
            start = i * FPS  # One new take per second, each lasts two seconds.
            duration = 2 * FPS
            expected[f'v{i}'] = start
            expected[f'a{i}'] = start
            video.append({'body': xml_clip(f'v{i}', start, duration,
                                          (f'a{i}', 'audio', i + 1))})
            audio.append({'body': xml_clip(f'a{i}', start, duration,
                                          (f'v{i}', 'video', i + 1))})
        project = Project(make_xml(video, audio))
        original_start = {clip.uid:clip.start for clip in project.clips}
        result = project.to_xml()
        self.assertEqual(result, project.to_xml(), 'Export must be idempotent')
        root = ET.fromstring(result)
        self.assertEqual(len(root.findall('./sequence/media/video/track')), 2)
        self.assertEqual(len(root.findall('./sequence/media/audio/track')), 2)
        self.assertEqual(project.lanes_added, 0)
        self.assertEqual(project.tracks_compacted, 54)
        self.assertEqual(len(root.findall('.//clipitem')), 58)
        assert_no_overlap(self, root, 'video')
        assert_no_overlap(self, root, 'audio')
        imported = Project(result)
        for clip in imported.clips:
            self.assertAlmostEqual(clip.start, original_start[clip.uid])
            linked = clip.element.find('./link')
            self.assertIsNotNone(linked)
            link_kind = linked.findtext('mediatype')
            idx = int(linked.findtext('trackindex'))
            clip_idx = int(linked.findtext('clipindex'))
            found = root.findall(f'./sequence/media/{link_kind}/track')[idx - 1]
            self.assertEqual(found.findall('clipitem')[clip_idx - 1].get('id'),
                             linked.findtext('linkclipref'))

    def test_true_concurrent_recordings_keep_required_tracks(self):
        video = [
            {'body': xml_clip(f'v{i}', 0, 8 * FPS)} for i in range(12)
        ]
        project = Project(make_xml(video))
        root = ET.fromstring(project.to_xml())
        # 12 overlapping cameras cannot physically coexist on fewer than 12 lanes.
        self.assertEqual(len(root.findall('./sequence/media/video/track')), 12)
        assert_no_overlap(self, root, 'video')

    def test_moved_clips_pack_across_original_tracks(self):
        video = [{'body': xml_clip(f'v{i}', i * 100, 40)} for i in range(8)]
        project = Project(make_xml(video))
        # Audio alignment has determined non-overlapping final positions.
        for i, clip in enumerate(project.clips):
            clip.new_start = i * 2
        root = ET.fromstring(project.to_xml())
        self.assertEqual(len(root.findall('./sequence/media/video/track')), 1)
        self.assertEqual(project.tracks_compacted, 7)
        assert_no_overlap(self, root, 'video')

    def test_different_audio_output_channels_must_remain_separate(self):
        audio = [
            {'metadata': '<enabled>TRUE</enabled><outputchannelindex>1</outputchannelindex>',
             'body': xml_clip('a1', 0, 50)},
            {'metadata': '<enabled>TRUE</enabled><outputchannelindex>2</outputchannelindex>',
             'body': xml_clip('a2', 50, 50)}
        ]
        project = Project(make_xml([], audio))
        root = ET.fromstring(project.to_xml())
        tracks = root.findall('./sequence/media/audio/track')
        self.assertEqual(len(tracks), 2)
        self.assertEqual([t.findtext('outputchannelindex') for t in tracks],
                         ['1','2'])

    def test_links_count_retained_opaque_clipitems_in_protected_tracks(self):
        opaque = ('<clipitem id="opaque"><start>-1</start><end>50</end>'
                  '<in>0</in><out>50</out></clipitem>')
        video = [{'body': opaque + xml_clip('v1', 0, 50, ('a1','audio',1))}]
        audio = [{'body': xml_clip('a1', 0, 50, ('v1','video',1))}]
        project = Project(make_xml(video, audio))
        root = ET.fromstring(project.to_xml())
        track = root.find('./sequence/media/video/track')
        self.assertEqual([c.get('id') for c in track.findall('clipitem')],
                         ['opaque','v1'])
        audio_link = root.find('.//clipitem[@id="a1"]/link')
        self.assertEqual(audio_link.findtext('trackindex'), '1')
        self.assertEqual(audio_link.findtext('clipindex'), '2')

    def test_opaque_transitions_and_unparsed_clips_are_preserved(self):
        opaque = ('<transitionitem id="fade"><start>49</start><end>60</end></transitionitem>'
                  '<clipitem id="opaque"><start>-1</start><end>90</end>'
                  '<in>0</in><out>90</out></clipitem>')
        video = [
            {'body': xml_clip('v1', 0, 50) + opaque},
            {'body': xml_clip('v2', 50, 50)}
        ]
        project = Project(make_xml(video))
        root = ET.fromstring(project.to_xml())
        self.assertEqual(len(root.findall('.//transitionitem[@id="fade"]')), 1)
        self.assertEqual(len(root.findall('.//clipitem[@id="opaque"]')), 1)
        self.assertEqual(len(root.findall('.//clipitem[@id="v1"]')), 1)
        self.assertEqual(len(root.findall('.//clipitem[@id="v2"]')), 1)
        self.assertEqual(len(root.findall('./sequence/media/video/track')), 2)


if __name__ == '__main__':
    unittest.main()
