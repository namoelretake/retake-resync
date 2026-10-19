"""Regression checks for ReSync 0.6.9 native media-folder entry and restart UX."""
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestMediaFolderUX(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "source" / "index.html").read_text(encoding="utf8")
        cls.native = (ROOT / "native" / "ReSyncNative.swift").read_text(encoding="utf8")

    def test_native_app_has_standard_mac_edit_shortcuts(self):
        self.assertIn("installStandardEditMenu()", self.native)
        self.assertIn('("Colar", "paste:", "v")', self.native)
        self.assertIn('("Copiar", "copy:", "c")', self.native)
        self.assertIn('("Selecionar Tudo", "selectAll:", "a")', self.native)
        self.assertIn("item.keyEquivalentModifierMask = [.command]", self.native)
        self.assertIn("NSApp.mainMenu = menu", self.native)

    def test_native_clipboard_and_folder_chooser_return_to_web_input(self):
        self.assertIn('action == "pasteMediaPath"', self.native)
        self.assertIn("NSPasteboard.general.string(forType: .string)", self.native)
        self.assertIn('action == "chooseMediaFolder"', self.native)
        self.assertIn("panel.canChooseDirectories = true", self.native)
        self.assertIn("panel.canChooseFiles = false", self.native)
        self.assertIn("sendMediaPathToUI(folder.path)", self.native)
        self.assertIn("JSONSerialization.data(withJSONObject: payload)", self.native)
        self.assertIn("resyncMediaPath", self.native)

    def test_missing_media_dialog_has_two_functional_actions(self):
        for widget in ("folder", "pastemedia", "browsemedia", "continuemedia"):
            self.assertEqual(len(re.findall(r'id="' + widget + r'"', self.html)), 1)
        self.assertIn("native.postMessage({action:'pasteMediaPath'})", self.html)
        self.assertIn("native.postMessage({action:'chooseMediaFolder'})", self.html)
        self.assertIn("window.addEventListener('resyncMediaPath'", self.html)
        self.assertIn("setMediaFolderPath(event.detail?.path)", self.html)
        self.assertIn("$('folder').value=path.trim()", self.html)

    def test_new_project_is_available_on_both_completion_screens(self):
        self.assertEqual(len(re.findall(r'id="donenewproject"', self.html)), 1)
        self.assertEqual(len(re.findall(r'id="newproject"', self.html)), 1)
        self.assertIn("$('donenewproject').onclick=newProject", self.html)
        self.assertIn("$('newproject').onclick=newProject", self.html)
        self.assertIn("clearTimeout(pollTimer)", self.html)
        self.assertIn("result=null;", self.html)
        self.assertIn("project=null;", self.html)
        self.assertIn("$('folder').value=''", self.html)
        self.assertIn("screen('welcome')", self.html)

    def test_inline_javascript_parses(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed")
        match = re.search(r"<script>(.*?)</script>", self.html, re.S)
        self.assertIsNotNone(match)
        proc = subprocess.run(
            [node, "--check", "-"], input=match.group(1),
            text=True, capture_output=True, timeout=12,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_version_matches_app_bundle(self):
        plist = (ROOT / "build" / "Info.plist").read_text(encoding="utf8")
        self.assertIn("<key>CFBundleShortVersionString</key><string>0.6.9</string>", plist)
        self.assertIn("<key>CFBundleVersion</key><string>9</string>", plist)
        self.assertIn("ReSync <small>v0.6.9</small>", self.html)


if __name__ == "__main__":
    unittest.main()
