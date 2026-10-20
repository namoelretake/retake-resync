#!/usr/bin/env python3
"""Reproducible end-to-end audio-sync benchmark, with no downloaded media.

Examples (run from the repository root):
  python build/benchmark_sync.py --engine source/engine.py --scenario multicam
  python build/benchmark_sync.py --engine source/engine.py \
      --baseline ../engine_before.py --scenario long --output benchmark.json

The first run starts with an empty, isolated ReSync cache; the second reuses it.
OS filesystem caches are not flushed. Fixtures are deterministic PCM WAVs or
AAC M4As, so these numbers do not predict the runtime of a user's MP4/MOV project. The
baseline and candidate run sequentially, in separate Python processes, against
the exact same media, XML, and thresholds. Timings exclude fixture creation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import wave
import xml.etree.ElementTree as ET

import numpy as np


SR = 16000
FPS = 25
SCENARIOS = {
    # User-reported workload: eight cameras, roughly three minutes per take.
    "user-8x180": {"cameras": 8, "blocks": 1, "duration": 180},
    # Three independent cameras, repeated separate recording blocks.
    "quick": {"cameras": 3, "blocks": 3, "duration": 20},
    "multicam": {"cameras": 3, "blocks": 8, "duration": 45},
    "many": {"cameras": 3, "blocks": 20, "duration": 20},
    # One continuous recorder plus 24 short takes spread over 15 minutes.
    "long": {"cameras": 3, "blocks": 8, "duration": 45, "reference": 900},
}


def load_engine(path: Path):
    spec = importlib.util.spec_from_file_location("resync_benchmark_engine", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_wav(path: Path, samples: np.ndarray):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, SR, 0, "NONE", "not compressed"))
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def encode_fixture(path: Path, media_format: str) -> Path:
    if media_format == "wav":
        return path
    encoded = path.with_suffix(".m4a")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(path), "-ac", "2", "-ar", "48000", "-c:a", "aac",
                    "-b:a", "192k", str(encoded)], check=True, capture_output=True)
    path.unlink()
    return encoded


def clip(track: ET.Element, uid: str, path: Path, duration: float,
         start: float = 0, linked: str = ""):
    item = ET.SubElement(track, "clipitem", {"id": uid})
    for key, value in (("name", uid), ("start", round(start * FPS)),
                       ("end", round((start + duration) * FPS)),
                       ("in", 0), ("out", round(duration * FPS))):
        ET.SubElement(item, key).text = str(value)
    source = ET.SubElement(item, "file", {"id": "file-" + uid})
    ET.SubElement(source, "pathurl").text = path.as_uri()
    if linked:
        link = ET.SubElement(item, "link")
        ET.SubElement(link, "linkclipref").text = linked


def create_fixture(folder: Path, scenario: str, media_format: str) -> dict:
    settings = SCENARIOS[scenario]
    blocks, cameras, duration = (settings[k] for k in ("blocks", "cameras", "duration"))
    span = settings.get("reference", blocks * (duration + max(12, cameras * 2 + 1)))
    rng = np.random.default_rng(472711)
    # A unique broadband, varying event stream supplies identifiable overlaps.
    # Independent camera gain/noise and a simple mic-response filter avoid
    # merely comparing byte-identical recordings.
    frames = int(span * SR)
    envelope = np.repeat(rng.uniform(.08, .8, size=(frames + 1599) // 1600), 1600)[:frames]
    master = (rng.normal(size=frames).astype(np.float32) * envelope * .24)
    master[1:] += master[:-1] * .15
    root = ET.Element("xmeml", {"version": "5"})
    sequence = ET.SubElement(root, "sequence")
    ET.SubElement(sequence, "name").text = "ReSync deterministic benchmark"
    rate = ET.SubElement(sequence, "rate")
    ET.SubElement(rate, "timebase").text = str(FPS)
    ET.SubElement(rate, "ntsc").text = "FALSE"
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    audio = ET.SubElement(media, "audio")
    expected_blocks = {}
    decoded_seconds = 0
    if settings.get("reference"):
        path = folder / "recorder.wav"
        write_wav(path, master)
        path = encode_fixture(path, media_format)
        clip(ET.SubElement(audio, "track"), "recorder", path, span)
        decoded_seconds += span
    for camera in range(cameras):
        vtrack = ET.SubElement(video, "track")
        atrack = ET.SubElement(audio, "track")
        for block in range(blocks):
            source_start = block * (span - duration - (cameras * 2 + 1)) / max(1, blocks - 1) + camera * 2
            source_start = round(source_start * FPS) / FPS
            begin = round(source_start * SR)
            samples = master[begin:begin + duration * SR].copy()
            assert len(samples) == duration * SR, "Fixture source is shorter than its XML duration"
            samples *= .55 + camera * .15
            if camera:
                samples[1:] += samples[:-1] * camera * .12
            samples += rng.normal(0, .0025, len(samples)).astype(np.float32)
            name = f"camera-{camera + 1}-take-{block + 1}"
            path = folder / (name + ".wav")
            write_wav(path, samples)
            path = encode_fixture(path, media_format)
            old_start = block * (duration + 5)
            clip(vtrack, name, path, duration, old_start, name + "-audio")
            clip(atrack, name + "-audio", path, duration, old_start, name)
            expected_blocks[name] = {"block": block, "source_start": source_start}
            decoded_seconds += duration
    xml = folder / "project.xml"
    xml.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    result = {"scenario": scenario, "groups": cameras * blocks + bool(settings.get("reference")),
              "media_format": "AAC 192kbps, 48kHz, stereo" if media_format == "aac" else "PCM 16-bit, 16kHz, mono",
              "expected_components": 1 if settings.get("reference") else blocks,
              "decoded_audio_seconds": decoded_seconds,
              "take_duration_seconds": duration,
              "continuous_recorder_seconds": settings.get("reference", 0),
              "expected": expected_blocks}
    (folder / "fixture.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def validate_result(result, fixture):
    expected = fixture["expected"]
    actual = {group["name"]: group for group in result["groups"]}
    if result["unmatched"]:
        raise AssertionError(f"Fixture has unexpected unmatched takes: {result['unmatched']}")
    if result["verified_components"] != fixture["expected_components"]:
        raise AssertionError(f"Unexpected number of verified blocks: {result['verified_components']}")
    for name, info in expected.items():
        peers = [(other, detail) for other, detail in expected.items()
                 if detail["block"] == info["block"]]
        anchor, detail = min(peers)
        delta = actual[name]["new"] - actual[anchor]["new"]
        target = info["source_start"] - detail["source_start"]
        if abs(delta - target) > .04:
            raise AssertionError(f"Offset is incorrect for {name}: {delta} versus {target}")
        if fixture["continuous_recorder_seconds"]:
            delta = actual[name]["new"] - actual["recorder"]["new"]
            if abs(delta - info["source_start"]) > .04:
                raise AssertionError(f"Recorder offset is incorrect for {name}")


def run_worker(args):
    engine = load_engine(Path(args.engine).resolve())
    fixture_path = Path(args.fixture)
    fixture = json.loads((fixture_path / "fixture.json").read_text())
    xml = (fixture_path / "project.xml").read_bytes()
    observations = []
    for trial in range(args.repeats):
        phase = {}
        original_correlate = engine.correlate_features
        def correlate(a, b):
            phase.setdefault("compare_start", time.perf_counter())
            return original_correlate(a, b)
        def progress(pct, message):
            if pct == 3:
                phase.setdefault("extract_start", time.perf_counter())
            if pct == 20:
                phase.setdefault("compare_start", time.perf_counter())
            if pct == 81:
                phase["compare_end"] = time.perf_counter()
        engine.correlate_features = correlate
        started = time.perf_counter()
        project = engine.Project(xml)
        result = engine.sync(project, progress=progress)
        synced = time.perf_counter()
        output = project.to_xml()
        finished = time.perf_counter()
        engine.correlate_features = original_correlate
        validate_result(result, fixture)
        observations.append({"trial": trial + 1,
            "cache": "empty" if trial == 0 else "reused",
            "seconds": round(finished - started, 6),
            "sync_seconds": round(synced - started, 6),
            "extract_seconds": round(phase["compare_start"] - phase["extract_start"], 6),
            "compare_seconds": round(phase["compare_end"] - phase["compare_start"], 6),
            "export_seconds": round(finished - synced, 6),
            "comparisons": result["comparisons"],
            "verified_components": result["verified_components"],
            "result_sha256": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
            "xml_sha256": hashlib.sha256(output).hexdigest()})
    print(json.dumps(observations))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", type=Path, default=Path(__file__).resolve().parents[1] / "source/engine.py")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--scenario", choices=SCENARIOS, default="multicam")
    parser.add_argument("--media-format", choices=("wav", "aac"), default="wav")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.worker:
        run_worker(args)
        return
    if not shutil.which("ffmpeg"):
        parser.error("ffmpeg must be available in PATH")
    report = {"platform": platform.platform(), "machine": platform.machine(),
              "python": platform.python_version(), "numpy": np.__version__,
              "ffmpeg": subprocess.run(["ffmpeg", "-version"], capture_output=True,
                  text=True, check=True).stdout.splitlines()[0],
              "workers": args.workers or "engine default",
              "notes": "Synthetic audio; isolated ReSync caches, OS filesystem caches not flushed; timings exclude fixture generation/encoding.",
              "engines": {}}
    with tempfile.TemporaryDirectory(prefix="resync-benchmark-") as temp:
        root = Path(temp).resolve()
        fixture = root / "media"
        fixture.mkdir()
        report["fixture"] = create_fixture(fixture, args.scenario, args.media_format)
        report["fixture"].pop("expected")
        engines = [("baseline", args.baseline)] if args.baseline else []
        engines.append(("candidate", args.engine))
        for label, path in engines:
            env = dict(os.environ)
            env["RESYNC_CACHE_DIR"] = str(root / ("cache-" + label))
            env.pop("RESYNC_DISABLE_CACHE", None)
            if args.workers is not None:
                env["RESYNC_WORKERS"] = str(args.workers)
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--engine", str(path.resolve()),
                       "--fixture", str(fixture), "--repeats", str(args.repeats)]
            result = subprocess.run(command, env=env, check=True, text=True, capture_output=True)
            report["engines"][label] = {"path": str(path.resolve()),
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "runs": json.loads(result.stdout)}
        if args.baseline:
            baseline = report["engines"]["baseline"]["runs"]
            candidate = report["engines"]["candidate"]["runs"]
            report["equivalent_results"] = all(r["result_sha256"] == baseline[0]["result_sha256"]
                and r["xml_sha256"] == baseline[0]["xml_sha256"] for r in baseline + candidate)
            if not report["equivalent_results"]:
                raise AssertionError("Candidate output or result differs from the baseline")
            report["speedup"] = {"first_run": round(baseline[0]["seconds"] / candidate[0]["seconds"], 3),
                                 "repeated_run": round(baseline[-1]["seconds"] / candidate[-1]["seconds"], 3)}
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
