"""Guard the packaging contract shared by Premiere UXP and ReSync.app."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestPremiereBridgeContract(unittest.TestCase):
    def test_native_macos_bundle_registers_url_scheme(self):
        plist = (ROOT / "build" / "Info.plist").read_text(encoding="utf8")
        self.assertIn("<key>CFBundleURLTypes</key>", plist)
        self.assertIn("<string>resync</string>", plist)
        native = (ROOT / "native" / "ReSyncNative.swift").read_text(encoding="utf8")
        self.assertIn("application(_ application: NSApplication, open urls: [URL])", native)
        self.assertIn('process.arguments = ["--resync-job", path]', native)
        self.assertIn("backend/resync-server/resync-server", native)
        self.assertIn("resync_[0-9a-f]{24}", native)

    def test_packaged_python_backend_accepts_headless_flag(self):
        app = (ROOT / "source" / "app.py").read_text(encoding="utf8")
        self.assertIn("sys.argv[1] == '--resync-job'", app)
        self.assertIn("from sync_job import run_job", app)

    def test_uxp_and_macos_share_the_same_scheme_and_protocol(self):
        manifest = json.loads((ROOT / "premiere-uxp" / "manifest.json").read_text(encoding="utf8"))
        self.assertIn("resync", manifest["requiredPermissions"]["launchProcess"]["schemes"])
        js = (ROOT / "premiere-uxp" / "main.js").read_text(encoding="utf8")
        self.assertIn("resync://sync?job=", js)
        self.assertIn("protocol:1", js)
        self.assertIn("project.importFiles([xmlPath], true, null, false)", js)
        self.assertIn("project.openSequence(matched)", js)

    def test_python_job_file_name_is_locked_to_a_nonce(self):
        sys.path.insert(0, str(ROOT / "source"))
        from sync_job import JOB_PATTERN
        self.assertIsNotNone(JOB_PATTERN.fullmatch("resync_" + "a" * 24 + ".job.json"))
        self.assertIsNone(JOB_PATTERN.fullmatch("../other.xml"))
        self.assertIsNone(JOB_PATTERN.fullmatch("resync_" + "G" * 24 + ".job.json"))


if __name__ == "__main__":
    unittest.main()
