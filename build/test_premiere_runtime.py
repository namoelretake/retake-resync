#!/usr/bin/env python3
"""Exercise the actual frozen plugin worker with synthetic audio and XML.

This proves a self-contained runtime, not loading an Adobe addon or importing
the result into Premiere. No installed ReSync.app or system FFmpeg is used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave
import xml.etree.ElementTree as ET

import numpy as np


SR = 16000
FPS = 25


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def songs(duration=30):
    # Identical, irregular attack timing but distinct notes: the former
    # rhythm-only matcher incorrectly merged these songs into one block.
    rng = np.random.default_rng(547)
    t = np.arange(SR * duration) / SR
    positions = np.arange(.1, duration, .43)
    positions += rng.uniform(-.12, .12, len(positions))
    gains = rng.uniform(.2, .9, len(positions))
    envelope = np.zeros_like(t)
    for position, gain in zip(positions, gains):
        envelope += (gain * np.exp(-np.maximum(t - position, 0) * 13)
                     * (t >= position) * (t < position + .3))
    return [np.sin(2 * np.pi * frequency * t) * envelope * .7
            for frequency in (490, 575)]


def write_audio(path: Path, samples):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, SR, 0, "NONE", "not compressed"))
        stream.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def make_fixture(folder: Path) -> tuple[Path, dict, bytes]:
    job_id = "0123456789abcdef01234567"
    stem = "resync_" + job_id
    root = ET.Element("xmeml", version="5")
    seq = ET.SubElement(root, "sequence")
    ET.SubElement(seq, "name").text = "Músicas independentes — teste do plugin"
    ET.SubElement(seq, "duration").text = str(80 * FPS)
    rate = ET.SubElement(seq, "rate")
    ET.SubElement(rate, "timebase").text = str(FPS)
    ET.SubElement(rate, "ntsc").text = "FALSE"
    media = ET.SubElement(seq, "media")
    audio = ET.SubElement(media, "audio")
    tracks = [ET.SubElement(audio, "track") for _ in range(2)]
    hashes = {}
    for song_index, sound in enumerate(songs()):
        for camera in range(2):
            # Camera 2 begins two seconds later and has a distinct mic EQ.
            recording = sound[:28 * SR] if camera == 0 else sound[2 * SR:]
            if camera:
                recording = .7 * recording + .2 * np.r_[recording[0], recording[:-1]]
            name = f"music{song_index + 1}-camera{camera + 1}"
            path = folder / (name + ".wav")
            write_audio(path, recording)
            hashes[path] = sha256(path)
            item = ET.SubElement(tracks[camera], "clipitem", id=name)
            for tag, value in [("name", name), ("start", song_index * 40 * FPS),
                               ("end", (song_index * 40 + 28) * FPS), ("in", 0),
                               ("out", 28 * FPS)]:
                ET.SubElement(item, tag).text = str(value)
            source = ET.SubElement(item, "file", id="file-" + name)
            ET.SubElement(source, "name").text = path.name
            ET.SubElement(source, "pathurl").text = path.as_uri()
            file_rate = ET.SubElement(source, "rate")
            ET.SubElement(file_rate, "timebase").text = str(FPS)
    source_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    (folder / (stem + ".source.xml")).write_bytes(source_xml)
    job = folder / (stem + ".job.json")
    job.write_text(json.dumps({"protocol": 1, "job_id": job_id, "reference": "auto",
                               "sequence_name": "Músicas independentes"}), encoding="utf-8")
    return job, hashes, source_xml


def run(runtime: Path) -> dict:
    runtime = runtime.resolve()
    worker = runtime / "resync-premiere-worker" / "resync-premiere-worker"
    ffmpeg = runtime / "bin" / "ffmpeg"
    assert worker.is_file() and os.access(worker, os.X_OK), "Worker congelado ausente"
    assert ffmpeg.is_file() and os.access(ffmpeg, os.X_OK), "FFmpeg empacotado ausente"
    manifest = json.loads((runtime / "runtime.json").read_text(encoding="utf-8"))
    assert sha256(ffmpeg) == manifest["ffmpeg_sha256"], "FFmpeg difere dos metadados do runtime"
    assert sha256(worker) == manifest["worker_sha256"], "Worker difere dos metadados do runtime"
    assert manifest["third_party_notices"], "Avisos das dependências ausentes"
    assert all((runtime / notice).is_file() for notice in manifest["third_party_notices"]), manifest
    env = os.environ.copy()
    env["PATH"] = ""
    env["RESYNC_DISABLE_CACHE"] = "1"
    env["RESYNC_BUNDLE_ROOT"] = "/nonexistent/ReSync.app"
    env["RESYNC_PYTHON"] = "/nonexistent/python"
    env["IMAGEIO_FFMPEG_EXE"] = "/nonexistent/ambient-ffmpeg"
    version = subprocess.run([str(worker), "--version-json"], env=env,
                             capture_output=True, text=True, timeout=30, check=True)
    metadata = json.loads(version.stdout)
    assert metadata["frozen"] is True, "Teste não executou o binário PyInstaller"
    assert metadata["job_protocol"] == metadata["worker_cli_protocol"] == 1, metadata
    assert metadata["engine_version"] == manifest["engine_version"], metadata
    with tempfile.TemporaryDirectory(prefix="ReSync plugin — ") as temporary:
        folder = Path(temporary) / "Projeto com espaços e acentos"
        folder.mkdir()
        job, media_hashes, source_xml = make_fixture(folder)
        result = subprocess.run([str(worker), "--job", str(job)], env=env, cwd=folder,
                                text=True, capture_output=True, timeout=180)
        assert result.returncode == 0, (result.returncode, result.stdout[-4000:], result.stderr[-4000:])
        stem = job.name.removesuffix(".job.json")
        state = json.loads((folder / (stem + ".status.json")).read_text(encoding="utf-8"))
        assert state["state"] == "done", state
        assert state["summary"]["total"] == 4, state
        assert state["summary"]["unmatched"] == 0, state
        assert state["summary"]["verified_components"] == 2, state
        assert not state["summary"]["collisions"], state
        assert state["output"] == stem + ".synced.xml", state
        output = ET.fromstring((folder / state["output"]).read_bytes())
        assert state["sequence_name"] == output.findtext("./sequence/name"), state
        assert "ReSync" in state["sequence_name"], state
        clips = {item.get("id"): item for item in output.findall(".//clipitem")}
        assert len(clips) == 4, list(clips)
        starts = {name: int(item.findtext("start")) / FPS for name, item in clips.items()}
        ends = {name: int(item.findtext("end")) / FPS for name, item in clips.items()}
        for number in (1, 2):
            lag = starts[f"music{number}-camera2"] - starts[f"music{number}-camera1"]
            assert abs(lag - 2) <= 1 / FPS, starts
        first_end = max(end for name, end in ends.items() if name.startswith("music1-"))
        second_start = min(start for name, start in starts.items() if name.startswith("music2-"))
        assert second_start - first_end >= 10 - 1 / FPS, (starts, ends)
        assert (folder / (stem + ".source.xml")).read_bytes() == source_xml, "XML original modificado"
        assert all(sha256(path) == digest for path, digest in media_hashes.items()), "Mídia original modificada"
        # This is an installation error, not permission to fall back to a
        # globally installed FFmpeg. Hide our binary temporarily to prove it.
        hidden = ffmpeg.with_name("ffmpeg.smoke-hidden")
        ffmpeg.rename(hidden)
        try:
            missing = subprocess.run([str(worker), "--version-json"], env=env,
                                     text=True, capture_output=True, timeout=30)
            assert missing.returncode == 2 and "FFmpeg" in missing.stderr, missing
        finally:
            hidden.rename(ffmpeg)
        return {"passed": True, "frozen": True, "engine_version": metadata["engine_version"],
                "architecture": metadata["architecture"], "job_protocol": 1,
                "ambient_path": "", "verified_components": 2, "takes": 4,
                "true_lag_seconds": 2, "block_gap_seconds": round(second_start - first_end, 3),
                "source_xml_and_media_unchanged": True, "missing_bundled_ffmpeg_rejected": True,
                "elapsed_s": state["elapsed_s"]}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python build/test_premiere_runtime.py /pasta/mac/arm64/runtime")
    print(json.dumps(run(Path(sys.argv[1])), ensure_ascii=False, sort_keys=True))
