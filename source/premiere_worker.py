"""Dedicated, headless worker bundled inside the ReSync Premiere plugin.

This entrypoint has no app launcher, HTTP server, GUI or URL handler. The
native addon will launch this executable with an argv list and one job path.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys

from sync_job import run_job


WORKER_CLI_PROTOCOL = 1
JOB_PROTOCOL = 1
WORKER_RELATIVE_PATH = "resync-premiere-worker/resync-premiere-worker"
FFMPEG_RELATIVE_PATH = "bin/ffmpeg"


def runtime_root() -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Este worker deve ser executado pelo runtime empacotado do plugin.")
    return Path(sys.executable).resolve().parent.parent


def load_metadata(root: Path) -> dict:
    metadata = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or (
        metadata.get("schema_version") != 1
        or metadata.get("worker_cli_protocol") != WORKER_CLI_PROTOCOL
        or metadata.get("job_protocol") != JOB_PROTOCOL
        or metadata.get("worker_relative_path") != WORKER_RELATIVE_PATH
        or metadata.get("ffmpeg_relative_path") != FFMPEG_RELATIVE_PATH
        or metadata.get("platform") != sys.platform
        or metadata.get("architecture") != platform.machine()
        or not isinstance(metadata.get("engine_version"), str)
        or not metadata["engine_version"]
    ):
        raise ValueError("Metadados do runtime ReSync incompatíveis.")
    return metadata


def configure_runtime(root: Path) -> Path:
    """Use only the FFmpeg delivered alongside this worker, never PATH fallback."""
    ffmpeg = root / FFMPEG_RELATIVE_PATH
    if ffmpeg.is_symlink() or not ffmpeg.is_file() or not os.access(ffmpeg, os.X_OK):
        raise RuntimeError("FFmpeg empacotado ausente ou sem permissão de execução.")
    os.environ["PATH"] = str(ffmpeg.parent)
    return ffmpeg


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Motor local do ReSync para Premiere")
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--job", type=Path, help="Caminho absoluto do resync_ID.job.json")
    command.add_argument("--version-json", action="store_true", help="Metadados e protocolos do runtime")
    args = parser.parse_args(argv)
    try:
        root = (root or runtime_root()).resolve()
        metadata = load_metadata(root)
        configure_runtime(root)
        if args.version_json:
            print(json.dumps({**metadata, "frozen": bool(getattr(sys, "frozen", False))},
                             ensure_ascii=False, sort_keys=True))
            return 0
        if not args.job.is_absolute():
            raise ValueError("O caminho do trabalho deve ser absoluto.")
        return run_job(args.job)
    except Exception as exc:
        print(f"ReSync Premiere worker: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
