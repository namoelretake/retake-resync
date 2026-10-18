"""Regression checks for the native macOS XML picker integration.

The actual NSOpenPanel behaviour still requires testing the built .app on macOS.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestNativeXMLPicker(unittest.TestCase):
    def test_webkit_file_picker_has_native_open_panel_delegate(self):
        swift = (ROOT / "native" / "ReSyncNative.swift").read_text(encoding="utf-8")
        self.assertIn("WKUIDelegate", swift)
        self.assertIn("view.uiDelegate = self", swift)
        self.assertIn("runOpenPanelWith parameters: WKOpenPanelParameters", swift)
        self.assertIn("NSOpenPanel()", swift)
        self.assertIn("panel.allowedContentTypes = [UTType.xml]", swift)
        self.assertIn("completionHandler(response == .OK ? panel.urls : nil)", swift)

    def test_xml_import_click_opens_real_input(self):
        html = (ROOT / "source" / "index.html").read_text(encoding="utf-8")
        match = re.search(r'<input type="file" id="fileinput"([^>]*)>', html)
        self.assertIsNotNone(match)
        attrs = match.group(1)
        self.assertIn('accept=".xml,text/xml,application/xml"', attrs)
        self.assertNotRegex(attrs, r'\bhidden\b')
        self.assertIn("drop.addEventListener('click',openXMLFilePicker)", html)
        self.assertIn("input.click()", html)
        self.assertIn("$('fileinput').onchange", html)

    def test_drag_and_drop_import_still_available(self):
        html = (ROOT / "source" / "index.html").read_text(encoding="utf-8")
        self.assertIn("drop.addEventListener('drop',handleDrop)", html)
        self.assertIn("importXML(files[0])", html)


if __name__ == "__main__":
    unittest.main()
