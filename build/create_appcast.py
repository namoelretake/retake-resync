#!/usr/bin/env python3
"""Build a minimal signed Sparkle 2 appcast for a public GitHub Release.

Sparkle verifies the EdDSA signature on the archive. Never add a private key
to this file, to the appcast, or to the app bundle.
"""
from __future__ import annotations

import argparse
import base64
import html
import re
from pathlib import Path
from urllib.parse import urlparse


def appcast(version: str, build: str, archive: Path, signature: str, url: str) -> str:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version):
        raise ValueError("Invalid release version")
    if not re.fullmatch(r"[0-9]+", build) or int(build) <= 0:
        raise ValueError("Invalid incremental build number")
    try:
        raw_signature = base64.b64decode(signature, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Invalid Sparkle EdDSA signature") from exc
    if len(raw_signature) != 64:
        raise ValueError("Sparkle EdDSA signature must be 64 bytes")
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "github.com":
        raise ValueError("Release URL must be HTTPS on github.com")
    if not archive.is_file() or archive.stat().st_size <= 0:
        raise ValueError("Update archive missing or empty")
    esc = lambda value: html.escape(str(value), quote=True)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>ReSync — atualizações</title>
    <link>https://github.com/namoelretake/retake-resync/releases</link>
    <description>Versões oficiais do ReSync para Apple Silicon.</description>
    <language>pt-br</language>
    <item>
      <title>ReSync {esc(version)}</title>
      <sparkle:version>{esc(build)}</sparkle:version>
      <sparkle:shortVersionString>{esc(version)}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>12.0.0</sparkle:minimumSystemVersion>
      <sparkle:hardwareRequirements>arm64</sparkle:hardwareRequirements>
      <enclosure url="{esc(url)}" sparkle:edSignature="{esc(signature)}"
                 length="{archive.stat().st_size}" type="application/octet-stream" />
    </item>
  </channel>
</rss>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="saida/appcast.xml", type=Path)
    args = parser.parse_args()
    xml = appcast(args.version, args.build, args.archive, args.signature, args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(xml, encoding="utf-8")


if __name__ == "__main__":
    main()
