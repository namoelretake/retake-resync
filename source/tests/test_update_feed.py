"""Sparkle appcast contract tests (no network or macOS required)."""
import base64
import importlib.util
import plistlib
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "build" / "create_appcast.py"
SPEC = importlib.util.spec_from_file_location("resync_create_appcast", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class TestSparkleAppcast(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive = Path(self.tmp.name) / "ReSync-0.6.3-macOS-arm64.zip"
        self.archive.write_bytes(b"test signed archive")
        self.signature = base64.b64encode(bytes(range(64))).decode()
        self.url = ("https://github.com/namoelretake/retake-resync/"
                    "releases/download/v0.6.3/ReSync-0.6.3-macOS-arm64.zip")

    def test_valid_sparkle_rss_and_versions(self):
        xml = MOD.appcast("0.6.3", "3", self.archive, self.signature, self.url)
        root = ET.fromstring(xml)
        ns = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle"}
        item = root.find("./channel/item")
        self.assertEqual(item.findtext("sparkle:version", namespaces=ns), "3")
        self.assertEqual(item.findtext("sparkle:shortVersionString", namespaces=ns), "0.6.3")
        self.assertEqual(item.findtext("sparkle:hardwareRequirements", namespaces=ns), "arm64")
        info = plistlib.loads((SCRIPT.parent / "Info.plist").read_bytes())
        minimum = item.findtext("sparkle:minimumSystemVersion", namespaces=ns)
        self.assertEqual(tuple(int(v) for v in minimum.split('.'))[:2],
                         tuple(int(v) for v in info['LSMinimumSystemVersion'].split('.'))[:2])
        enclosure = item.find("enclosure")
        self.assertEqual(enclosure.attrib["url"], self.url)
        self.assertEqual(enclosure.attrib["{%s}edSignature" % ns["sparkle"]], self.signature)
        self.assertEqual(int(enclosure.attrib["length"]), self.archive.stat().st_size)

    def test_invalid_signature_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "signature"):
            MOD.appcast("0.6.3", "3", self.archive, "not-base64", self.url)

    def test_public_https_release_is_required(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            MOD.appcast("0.6.3", "3", self.archive, self.signature, "http://example.com/build.zip")

    def test_archive_must_exist(self):
        with self.assertRaisesRegex(ValueError, "archive"):
            MOD.appcast("0.6.3", "3", self.archive.with_name("missing.zip"), self.signature, self.url)


if __name__ == "__main__":
    unittest.main()

