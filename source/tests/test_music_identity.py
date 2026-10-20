"""A shared rhythm alone is not proof of the same musical recording."""
from pathlib import Path
import sys
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Project, sync

SR = 16000
FPS = 25


def pitched_songs(duration=30):
    rng = np.random.default_rng(547)
    t = np.arange(SR * duration) / SR
    positions = np.arange(.1, duration, .43)
    positions += rng.uniform(-.12, .12, len(positions))
    gains = rng.uniform(.2, .9, len(positions))
    envelope = np.zeros_like(t)
    for position, gain in zip(positions, gains):
        envelope += (gain * np.exp(-np.maximum(t - position, 0) * 13)
                     * (t >= position) * (t < position + .3))
    return [np.sin(2 * np.pi * frequency * t) * envelope * .7
            for frequency in (490, 575)]


def wav(path, sound):
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, SR, 0, 'NONE', 'not compressed'))
        stream.writeframes((np.clip(sound, -1, 1) * 32767).astype('<i2').tobytes())


class TestMusicalIdentity(unittest.TestCase):
    def test_irregular_shared_rhythm_with_different_pitches_forms_separate_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            songs = pitched_songs()
            root = ET.Element('xmeml', version='5')
            seq = ET.SubElement(root, 'sequence')
            rate = ET.SubElement(seq, 'rate')
            ET.SubElement(rate, 'timebase').text = str(FPS)
            media = ET.SubElement(seq, 'media')
            video = ET.SubElement(media, 'video')
            tracks = [ET.SubElement(video, 'track') for _ in range(2)]
            for song_index, song in enumerate(songs):
                for camera in range(2):
                    # The two camera recordings of each song are genuine
                    # matching material, with a different microphone response.
                    sound = song if not camera else .7 * song + .2 * np.r_[song[0], song[:-1]]
                    name = f'song{song_index + 1}-camera{camera + 1}'
                    path = Path(temporary) / (name + '.wav')
                    wav(path, sound)
                    clip = ET.SubElement(tracks[camera], 'clipitem', id=name)
                    for tag, value in [('name', name), ('start', song_index * 40 * FPS),
                                       ('end', (song_index * 40 + 30) * FPS),
                                       ('in', 0), ('out', 30 * FPS)]:
                        ET.SubElement(clip, tag).text = str(value)
                    file = ET.SubElement(clip, 'file', id='file-' + name)
                    ET.SubElement(file, 'pathurl').text = path.as_uri()
            project = Project(ET.tostring(root))
            result = sync(project)
            self.assertEqual(result['unmatched'], 0, result['groups'])
            self.assertEqual(result['verified_components'], 2, result['groups'])
            song1 = [g for g in project.groups if g.title.startswith('song1-')]
            song2 = [g for g in project.groups if g.title.startswith('song2-')]
            self.assertGreaterEqual(min(g.assigned for g in song2)
                                    - max(g.assigned + g.duration for g in song1), 10)
            exported = Project(project.to_xml())
            first_end = max(c.end for c in exported.clips if c.name.startswith('song1-'))
            second_start = min(c.start for c in exported.clips if c.name.startswith('song2-'))
            self.assertGreaterEqual(second_start - first_end, 10 - 1 / FPS)


if __name__ == '__main__':
    unittest.main()
