"""End-to-end tests of Premiere's isolated on-disk ReSync worker.

Audio correlation is mocked; the production sync() function is still covered
by the existing multi-camera audio tests.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Project
from sync_job import run_job

EXAMPLE_XML = b"""<?xml version="1.0"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence>
    <name>Entrevista</name>
    <duration>150</duration>
    <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
    <media><video><track>
      <clipitem id="video1"><name>CAM 1</name><start>0</start><end>150</end>
      <in>0</in><out>150</out>
      <file id="f1"><pathurl>file:///tmp/not-found-camera.mov</pathurl></file>
      </clipitem>
    </track></video></media>
  </sequence>
</xmeml>"""


class TestPremiereJob(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.base = Path(self.workspace.name)
        self.jobid = "ab" * 12
        self.prefix = "resync_" + self.jobid
        self.source = self.base / (self.prefix + ".source.xml")
        self.job = self.base / (self.prefix + ".job.json")
        self.status = self.base / (self.prefix + ".status.json")
        self.output = self.base / (self.prefix + ".synced.xml")
        self.source.write_bytes(EXAMPLE_XML)
        self.job.write_text(json.dumps({
            "job_id": self.jobid, "protocol": 1,
            "reference": "auto", "sequence_name": "Entrevista"
        }), encoding="utf8")

    def fake_sync(self, project, reference_track, media_folder, progress):
        self.assertEqual(reference_track, "auto")
        self.assertEqual(media_folder, "")
        progress(25, "Analisando mídia")
        for c in project.clips:
            c.new_start = c.start + 12.0
        progress(100, "Concluído")
        return {"total": 1, "synced": 1, "unmatched": 0,
                "verified_components": 1, "collisions": []}

    def test_job_produces_new_renamed_xml_and_keeps_source_intact(self):
        self.assertEqual(run_job(self.job, sync_fn=self.fake_sync), 0)
        state = json.loads(self.status.read_text(encoding="utf8"))
        self.assertEqual(state["state"], "done")
        self.assertEqual(state["percent"], 100)
        self.assertIn("ReSync", state["sequence_name"])
        self.assertEqual(state["output"], self.output.name)
        self.assertEqual(state["summary"]["video_tracks"], 0)
        self.assertEqual(state["summary"]["audio_tracks"], 1)
        self.assertGreaterEqual(state["summary"]["tracks_compacted"], 0)
        self.assertEqual(self.source.read_bytes(), EXAMPLE_XML)
        project = Project(self.output.read_bytes())
        self.assertEqual(project.seq.findtext("name"), state["sequence_name"])
        self.assertAlmostEqual(project.clips[0].start, 12.0)

    def test_failures_never_create_an_importable_xml(self):
        def fail(project, reference_track, media_folder, progress):
            progress(37, "Analisando...")
            raise RuntimeError("falha simulada no áudio")
        self.assertEqual(run_job(self.job, sync_fn=fail), 1)
        state = json.loads(self.status.read_text(encoding="utf8"))
        self.assertEqual(state["state"], "error")
        self.assertIn("falha simulada", state["message"])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), EXAMPLE_XML)

    def test_invalid_protocol_is_rejected_without_output(self):
        self.job.write_text(json.dumps({"job_id": self.jobid,"protocol": 999}), encoding="utf8")
        self.assertEqual(run_job(self.job, sync_fn=self.fake_sync), 1)
        self.assertFalse(self.output.exists())

    def test_worker_refuses_to_overwrite_previous_result(self):
        self.output.write_bytes(b"existing")
        with self.assertRaisesRegex(ValueError, "existe"):
            run_job(self.job, sync_fn=self.fake_sync)
        self.assertEqual(self.output.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
