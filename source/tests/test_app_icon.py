"""Regression tests for ReSync's packaged macOS app icon."""
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestAppIcon(unittest.TestCase):
    def test_selected_artwork_is_a_valid_webp_container(self):
        source = ROOT / "assets" / "ReSyncIconSource.webp"
        data = source.read_bytes()
        self.assertGreater(len(data), 1000)
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(data[8:12], b"WEBP")

    def test_macos_build_generates_png_and_icns_from_source(self):
        script = (ROOT / "build" / "Criar ReSync Completo.command").read_text(encoding="utf-8")
        self.assertIn('ICON_SOURCE="$BASE/assets/ReSyncIconSource.webp"', script)
        self.assertIn('scale=1024:1024:flags=lanczos', script)
        self.assertIn('iconutil -c icns "$ICONSET"', script)
        self.assertNotIn('cp "$BASE/assets/ReSyncIcon.icns"', script)

    def test_version_and_interface_agree(self):
        plist = (ROOT / "build" / "Info.plist").read_text(encoding="utf-8")
        html = (ROOT / "source" / "index.html").read_text(encoding="utf-8")
        match = re.search(r'<key>CFBundleShortVersionString</key><string>([0-9]+\\.[0-9]+\\.[0-9]+)</string>', plist)
        self.assertIsNotNone(match)
        version = match.group(1)
        self.assertRegex(plist, r'<key>CFBundleVersion</key><string>[1-9][0-9]*</string>')
        self.assertIn(f"ReSync <small>v{version}", html)


if __name__ == "__main__":
    unittest.main()
