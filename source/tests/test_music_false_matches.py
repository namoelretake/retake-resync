"""Different songs with similar grooves must remain separate sync blocks.

The files below are independently synthesized performances. Their melodies and
snare waveforms differ; matching their tempo or drum envelopes is not evidence
that two cameras recorded the same performance.
"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import (AudioCache, Project, confirm_audio_identity,
                    correlate_features, sync)

SR = 16000
FPS = 25


def different_song(seed, duration=24, bpm=120):
    rng = np.random.default_rng(seed)
    groove = np.random.default_rng(1)
    count = int(duration * SR)
    audio = np.zeros(count, dtype=np.float32)
    for beat_number, beat in enumerate(np.arange(0, duration, 60 / bpm)):
        start = int(beat * SR)
        size = min(4000, count - start)
        t = np.arange(size) / SR
        amplitude = .4 * groove.uniform(.15, 1)
        audio[start:start + size] += (amplitude * np.sin(
            2 * np.pi * (65 * t - 30 * t * t)) * np.exp(-t * 25))
        if beat_number % 2:
            size = min(1600, count - start)
            t = np.arange(size) / SR
            audio[start:start + size] += .2 * np.exp(-t * 40) * rng.normal(size=size)
        size = min(int(.4 * SR), count - start)
        t = np.arange(size) / SR
        pitch = rng.choice([220, 246.94, 261.63, 293.66, 329.63, 349.23, 392, 440])
        phase = rng.uniform(-np.pi, np.pi)
        audio[start:start + size] += .3 * np.sin(2 * np.pi * pitch * t + phase) * np.exp(-t * 5)
    audio += rng.normal(0, .005, count)
    return audio


def camera_recording(audio, rng):
    filtered = .65 * audio + .35 * np.r_[audio[0], audio[:-1]]
    return filtered * .75 + rng.normal(0, .034, size=len(audio)).astype(np.float32)


def write_audio(path, audio):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SR)
        stream.writeframes((np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes())


def make_project(directory, sources):
    root = ET.Element('xmeml', version='5')
    sequence = ET.SubElement(root, 'sequence')
    rate = ET.SubElement(sequence, 'rate')
    ET.SubElement(rate, 'timebase').text = str(FPS)
    video = ET.SubElement(ET.SubElement(sequence, 'media'), 'video')
    for index, (name, audio) in enumerate(sources.items()):
        path = directory / (name + '.wav')
        write_audio(path, audio)
        track = ET.SubElement(video, 'track')
        clip = ET.SubElement(track, 'clipitem', id=name)
        start = 0 if name.endswith('1') else 30
        duration = len(audio) / SR
        for key, value in [('name', name), ('start', start * FPS),
                           ('end', int((start + duration) * FPS)),
                           ('in', 0), ('out', int(duration * FPS))]:
            ET.SubElement(clip, key).text = str(value)
        file_node = ET.SubElement(clip, 'file', id='f' + str(index))
        ET.SubElement(file_node, 'pathurl').text = path.as_uri()
    return Project(ET.tostring(root, encoding='utf-8'))


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for music audio regression')
class TestMusicFalseMatches(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'RESYNC_DISABLE_CACHE': '1'})
        env.start()
        self.addCleanup(env.stop)

    def test_different_songs_do_not_bridge_two_verified_camera_pairs(self):
        rng = np.random.default_rng(51)
        first = different_song(34, bpm=122)
        second = different_song(79, bpm=121)
        sources = {'A1': first, 'B1': camera_recording(first, rng),
                   'A2': second, 'B2': camera_recording(second, rng)}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            project = make_project(directory, sources)
            # Ensure the fixture contains two genuine camera matches. The
            # regression is a false bridge, not a failure to find those pairs.
            cache = AudioCache(cache_dir=False)
            for left, right in [('A1', 'B1'), ('A2', 'B2')]:
                a = cache.features(str(directory / (left + '.wav')), 0, 24)
                b = cache.features(str(directory / (right + '.wav')), 0, 24)
                evidence = correlate_features(a, b)
                self.assertAlmostEqual(evidence[0], 0, delta=.02)
                self.assertGreater(evidence[1], .9)
                self.assertGreater(evidence.margin, .1)
            result = sync(project)
        self.assertEqual(result['unmatched'], 0, result['groups'])
        self.assertEqual(result['verified_components'], 2, result['groups'])
        positions = {group.title: group.assigned for group in project.groups}
        self.assertAlmostEqual(positions['A1'], positions['B1'], delta=.04)
        self.assertAlmostEqual(positions['A2'], positions['B2'], delta=.04)
        self.assertGreaterEqual(positions['A2'] - positions['A1'], 24 + 10)

    def test_same_tempo_with_different_melodies_is_not_a_shared_recording(self):
        sources = {'A1': different_song(34, duration=8, bpm=119),
                   'A2': different_song(79, duration=8, bpm=119)}
        with tempfile.TemporaryDirectory() as temporary:
            project = make_project(Path(temporary), sources)
            result = sync(project)
        self.assertEqual(result['unmatched'], 2, result['groups'])
        self.assertEqual(result['verified_components'], 0, result['groups'])

    def test_same_recording_with_mic_filters_noise_and_partial_overlap(self):
        rng = np.random.default_rng(418)
        original = different_song(34, duration=30, bpm=122)
        reverb = original.copy()
        for seconds, gain in [(.033, .5), (.091, .3), (.177, .2)]:
            delay = round(seconds * SR)
            reverb[delay:] += original[:-delay] * gain
        variants = {
            'lowpass': (np.convolve(original, np.ones(5) / 5, mode='same') * .75
                        + rng.normal(0, .034, len(original))),
            'highpass': ((original - np.convolve(original, np.ones(17) / 17,
                                                 mode='same')) * .8
                         + rng.normal(0, .02, len(original))),
            'reverb': reverb * .5 + rng.normal(0, .034, len(original)),
            'strong_noise': original * .6 + rng.normal(0, .07, len(original)),
        }
        for name, recording in variants.items():
            with self.subTest(microphone=name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                # Both positive and negative lags must inspect the same shared
                # interval, even with different lengths and a later camera in.
                sources = {'A1': original, 'A2': recording[4 * SR:24 * SR]}
                project = make_project(directory, sources)
                cache = AudioCache(cache_dir=False)
                a = cache.features(str(directory / 'A1.wav'), 0, 30)
                b = cache.features(str(directory / 'A2.wav'), 0, 20)
                forward, reverse = correlate_features(a, b), correlate_features(b, a)
                self.assertAlmostEqual(forward[0], 4, delta=.04)
                self.assertAlmostEqual(reverse[0], -4, delta=.04)
                self.assertTrue(confirm_audio_identity(cache, a, b, forward[0]))
                self.assertTrue(confirm_audio_identity(cache, b, a, reverse[0]))
                result = sync(project)
                self.assertEqual(result['unmatched'], 0, result['groups'])
                self.assertEqual(result['verified_components'], 1, result['groups'])
                positions = {group.title: group.assigned for group in project.groups}
                self.assertAlmostEqual(positions['A2'] - positions['A1'], 4, delta=.04)

    def test_shared_clap_with_silence_remains_valid_sync_evidence(self):
        rng = np.random.default_rng(27)
        original = np.zeros(SR * 20, dtype=np.float32)
        clap = rng.normal(0, .3, round(SR * .004))
        original[3 * SR:3 * SR + len(clap)] = clap
        shifted = np.r_[np.zeros(SR * 2), original[:-2 * SR]]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            project = make_project(directory, {'A1': original, 'A2': shifted})
            cache = AudioCache(cache_dir=False)
            a = cache.features(str(directory / 'A1.wav'), 0, 20)
            b = cache.features(str(directory / 'A2.wav'), 0, 20)
            evidence = correlate_features(a, b)
            # A shared physical marker is legitimate sync evidence, even when
            # the rest of both recordings is silent. Preserve slate workflows.
            self.assertTrue(confirm_audio_identity(cache, a, b, evidence[0]))
            result = sync(project)
        self.assertEqual(result['unmatched'], 0, result['groups'])
        self.assertEqual(result['verified_components'], 1, result['groups'])
        positions = {group.title: group.assigned for group in project.groups}
        self.assertAlmostEqual(positions['A2'] - positions['A1'], -2, delta=.04)

    def test_missing_verification_descriptor_cannot_confirm_a_match(self):
        cache = AudioCache(cache_dir=False)
        unknown = np.ones((200, 15), dtype=np.float32)
        self.assertIsNone(cache.verification_features(unknown))
        self.assertFalse(confirm_audio_identity(cache, unknown, unknown.copy(), 0))

    def test_silence_never_supplies_sync_evidence(self):
        silence = np.zeros(SR * 12, dtype=np.float32)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            project = make_project(directory, {'A1': silence, 'A2': silence.copy()})
            cache = AudioCache(cache_dir=False)
            a = cache.features(str(directory / 'A1.wav'), 0, 12)
            b = cache.features(str(directory / 'A2.wav'), 0, 12)
            self.assertIsNone(a)
            self.assertIsNone(b)
            self.assertFalse(confirm_audio_identity(cache, a, b, 0))
            result = sync(project)
        self.assertEqual(result['unmatched'], 2, result['groups'])
        self.assertEqual(result['verified_components'], 0, result['groups'])


if __name__ == '__main__':
    unittest.main()
