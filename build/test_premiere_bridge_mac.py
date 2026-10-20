#!/usr/bin/env python3
"""Packaged ReSync.app smoke test on a GitHub macOS runner.

This tests the PyInstaller worker and FFmpeg from the actual .app with a
synthetic WAV and FCP7 XML. The LaunchServices resync:// GUI handoff must be
checked on a real user Mac with Premiere; CI does not assert that part.
"""
from __future__ import annotations

import json
import math
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np


def run(bundle_path: Path) -> None:
    resources = bundle_path / "Contents" / "Resources"
    worker = resources / "backend" / "resync-server" / "resync-server"
    ffmpeg_folder = resources / "bin"
    plist = plistlib.loads((bundle_path / "Contents" / "Info.plist").read_bytes())
    schemes = [
        scheme
        for item in plist.get("CFBundleURLTypes", [])
        for scheme in item.get("CFBundleURLSchemes", [])
    ]
    assert "resync" in schemes, "macOS bundle must register resync:// scheme"
    assert worker.is_file(), "PyInstaller worker missing"
    assert (ffmpeg_folder / "ffmpeg").is_file(), "Bundled ffmpeg missing"
    # Never advertise an update for older macOS versions than the executable
    # can launch on. The native target is explicitly set from Info.plist.
    native_build = subprocess.run(
        ['xcrun', 'vtool', '-show-build', str(bundle_path / 'Contents/MacOS/ReSync')],
        check=True, capture_output=True, text=True,
    ).stdout
    minimums = re.findall(r'^\s*minos\s+([0-9.]+)', native_build, re.M)
    assert minimums, "Native executable deployment target could not be checked"
    def version_tuple(value):
        parts = tuple(int(part) for part in value.split('.'))
        return parts + (0,) * (3 - len(parts))
    declared = version_tuple(plist['LSMinimumSystemVersion'])
    assert all(version_tuple(value) <= declared for value in minimums), (
        'Native executable requires newer macOS than Info.plist advertises'
    )

    with tempfile.TemporaryDirectory(prefix="resync-bridge-test-") as temp:
        folder = Path(temp)
        audio = folder / "scratch.wav"
        samplerate = 8000
        duration = 13
        rng = np.random.default_rng(4488)
        t = np.arange(duration * samplerate) / samplerate
        signal = .28 * np.sin(2 * math.pi * 382 * t) + rng.normal(size=t.size) * .08
        packed = np.clip(signal * 32767, -32768, 32767).astype("<i2")
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setframerate(samplerate)
            stream.setsampwidth(2)
            stream.writeframes(packed.tobytes())

        jobid = "0123456789abcdef01234567"
        stem = "resync_" + jobid
        source = folder / (stem + ".source.xml")
        job = folder / (stem + ".job.json")
        status = folder / (stem + ".status.json")
        output = folder / (stem + ".synced.xml")
        xml = (
            '<?xml version="1.0"?><!DOCTYPE xmeml>'
            '<xmeml version="5"><sequence><name>Smoke Test</name>'
            '<duration>250</duration><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'
            '<media><audio><track><clipitem id="a1"><name>Audio Teste</name>'
            '<start>0</start><end>250</end><in>0</in><out>250</out>'
            f'<file id="wav"><pathurl>{audio.as_uri()}</pathurl></file>'
            '</clipitem></track></audio></media></sequence></xmeml>'
        ).encode("utf8")
        source.write_bytes(xml)
        job.write_text(json.dumps({
            "protocol": 1, "job_id": jobid, "sequence_name": "Smoke Test",
            "reference": "auto",
        }), encoding="utf8")

        env = os.environ.copy()
        env["PATH"] = str(ffmpeg_folder) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            [str(worker), "--resync-job", str(job)],
            cwd=str(worker.parent),
            env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=180, check=False,
        )
        if result.returncode:
            raise AssertionError(
                f"Packaged ReSync bridge worker failed: {result.returncode}\n"
                f"stdout: {result.stdout[-4000:]}\nstderr: {result.stderr[-4000:]}"
            )
        assert output.is_file(), "Worker did not generate output XML"
        state = json.loads(status.read_text(encoding="utf8"))
        assert state["state"] == "done", state
        assert state["job_id"] == jobid
        assert "ReSync" in state["sequence_name"], state
        assert state["output"] == output.name, state
        assert b"xmeml" in output.read_bytes(), "Invalid output XML"
        assert source.read_bytes() == xml, "Worker modified source XML"
        print("Bridge smoke test passed: packaged worker + FFmpeg + signed-style XML output")


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("saida/ReSync.app").resolve())

