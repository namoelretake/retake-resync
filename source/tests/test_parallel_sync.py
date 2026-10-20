"""Parallel analysis must preserve deterministic sync and source identity."""
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Project, sync


def project(paths=('one', 'two', 'three')):
    tracks = []
    for i, path in enumerate(paths):
        tracks.append(
            f'<track><clipitem id="c{i}"><name>{i}</name>'
            f'<start>{i * 500}</start><end>{i * 500 + 250}</end>'
            '<in>0</in><out>250</out>'
            f'<file id="f{i}"><pathurl>file:///tmp/{path}.wav</pathurl></file>'
            '</clipitem></track>')
    return Project(('<xmeml version="5"><sequence><name>Parallel</name>'
                    '<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'
                    '<media><audio>' + ''.join(tracks) +
                    '</audio></media></sequence></xmeml>').encode())


def feature(_self, path, _start, _duration):
    marker = {'one': 1, 'two': 2, 'three': 3}[Path(path).stem]
    return np.full((120, 5), marker, dtype=np.float32)


def match(a, b):
    return (float(b[0, 0] - a[0, 0]), .96)


class TestParallelSync(unittest.TestCase):
    def run_sync(self, workers, features=feature, paths=('one', 'two', 'three')):
        p = project(paths)
        with (patch.dict(os.environ, {'RESYNC_WORKERS': str(workers)}),
              patch('engine.os.cpu_count', return_value=4),
              patch('engine.shutil.which', return_value='/ffmpeg'),
              patch('engine.AudioCache.features', features),
              patch('engine.correlate_features', match),
              patch('engine.confirm_audio_identity', return_value=True)):
            result = sync(p)
        return result, p.to_xml()

    def test_parallel_and_serial_produce_identical_result_and_xml(self):
        serial = self.run_sync(1)
        parallel = self.run_sync(4)
        self.assertEqual(serial, parallel)
        self.assertEqual(serial[0]['comparisons'], 3)
        self.assertEqual(serial[0]['unmatched'], 0)

    def test_independent_audio_can_extract_concurrently(self):
        barrier = threading.Barrier(2, timeout=5)
        threads = set()
        lock = threading.Lock()

        def simultaneous(cache, path, start, duration):
            with lock:
                threads.add(threading.get_ident())
            barrier.wait()
            return feature(cache, path, start, duration)

        result, _ = self.run_sync(2, simultaneous, ('one', 'two'))
        self.assertEqual(len(threads), 2)
        self.assertEqual(result['unmatched'], 0)

    def test_repeated_segment_is_extracted_once_and_never_compared_to_itself(self):
        calls = []

        def counted(cache, path, start, duration):
            calls.append(Path(path).stem)
            return feature(cache, path, start, duration)

        serial = self.run_sync(1, paths=('one', 'one', 'two'))
        result = self.run_sync(4, counted, ('one', 'one', 'two'))
        self.assertEqual(result, serial)
        self.assertCountEqual(calls, ['one', 'two'])
        self.assertEqual(result[0]['comparisons'], 2)

    def test_malformed_media_url_is_reported_unavailable(self):
        p = project(('bad%00path',))
        with patch('engine.shutil.which', return_value='/ffmpeg'):
            result = sync(p)
        self.assertEqual(result['unmatched'], 1)
        self.assertEqual(result['comparisons'], 0)
        self.assertIn('não localizado', result['groups'][0]['result'])


if __name__ == '__main__':
    unittest.main()
