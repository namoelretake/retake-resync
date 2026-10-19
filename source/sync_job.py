"""Headless ReSync job runner for the Premiere UXP extension.

Transport: UXP exports XML and writes a job descriptor in a user-approved folder.
The native macOS ReSync.app receives a resync://sync URL and starts this worker.
All media and XML remain local. The source Premiere timeline is never modified.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from engine import Project, sync

JOB_PATTERN = re.compile(r"resync_([0-9a-f]{24})\.job\.json\Z")
MAX_XML_BYTES = 30_000_000
MAX_JOB_BYTES = 16_384


def _safe_label(value: str) -> str:
    """Human-readable sequence name, not a path or an XML fragment."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:120].strip() or "Sequencia"


def _atomic_status(destination: Path, data: dict) -> None:
    # The UXP panel reads this file concurrently; a replace avoids partial JSON.
    partial = destination.with_suffix(".tmp")
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with partial.open("w", encoding="utf-8") as f:
        f.write(encoded)
        f.flush()
        os.fsync(f.fileno())
    os.replace(partial, destination)


def run_job(job_path: str | Path, sync_fn=sync) -> int:
    path = Path(job_path).expanduser().resolve()
    match = JOB_PATTERN.fullmatch(path.name)
    if not match or not path.is_file() or path.stat().st_size > MAX_JOB_BYTES:
        raise ValueError("Arquivo de solicitação ReSync inválido.")
    # A job cannot choose arbitrary output/status paths. Every file belongs
    # to the same explicitly selected workspace, with a unique id.
    job_id = match.group(1)
    source = path.parent / f"resync_{job_id}.source.xml"
    target = path.parent / f"resync_{job_id}.synced.xml"
    status_file = path.parent / f"resync_{job_id}.status.json"

    if not source.is_file() or source.stat().st_size > MAX_XML_BYTES:
        raise ValueError("XML original ausente ou maior que 30 MB.")
    if target.exists():
        raise ValueError("Arquivo de saída deste trabalho já existe.")

    state = {"job_id": job_id, "state": "running", "percent": 0,
             "message": "Preparando áudio...", "output": target.name}
    started = time.monotonic()
    last_update = 0.0

    def report(percent, message):
        nonlocal last_update
        if percent >= 0:
            state["percent"] = max(0, min(100, int(percent)))
        state["message"] = str(message)[:250]
        now = time.monotonic()
        if now - last_update > .25 or percent >= 100:
            _atomic_status(status_file, state)
            last_update = now

    _atomic_status(status_file, state)
    try:
        params = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(params, dict) or params.get("job_id") != job_id:
            raise ValueError("Identificador do trabalho não confere.")
        if params.get("protocol") != 1:
            raise ValueError("Versão de protocolo ReSync incompatível.")
        media_folder = str(params.get("media_folder", "")).strip()
        reference = str(params.get("reference", "auto"))
        if media_folder and (not Path(media_folder).is_absolute() or len(media_folder) > 2048):
            raise ValueError("Pasta de mídia inválida.")

        source_xml = source.read_bytes()
        project = Project(source_xml)
        original_name = project.seq.findtext("name") or params.get("sequence_name", "Sequencia")
        report(1, "Importando clipes da sequência...")
        result = sync_fn(project, reference_track=reference,
                         media_folder=media_folder, progress=report)

        # Give Premiere a distinct sequence name; the original remains intact.
        unique_name = f"{_safe_label(original_name)} — ReSync {job_id[:6]}"
        name = project.seq.find("name")
        if name is None:
            name = ET.Element("name")
            project.seq.insert(0, name)
        name.text = unique_name
        project._export_cache = None
        data = project.to_xml()
        if not data or b"<xmeml" not in data[:512]:
            raise ValueError("A exportação gerou XML inválido.")
        # Do not allow a partially written output to be imported by Premiere.
        partial = target.with_suffix(".writing")
        partial.write_bytes(data)
        os.replace(partial, target)

        state.update(state="done", percent=100,
                     message=f"{result.get('total', 0)} takes processados. Abrindo sequência no Premiere...",
                     sequence_name=unique_name,
                     summary={k: result.get(k) for k in
                              ("synced", "unmatched", "total", "verified_components", "collisions")},
                     elapsed_s=round(time.monotonic() - started, 2))
        _atomic_status(status_file, state)
        return 0
    except Exception as exc:
        state.update(state="error", message=f"{type(exc).__name__}: {str(exc)[:300]}")
        _atomic_status(status_file, state)
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: sync_job.py /caminho/resync_ID.job.json", file=sys.stderr)
        sys.exit(2)
    sys.exit(run_job(sys.argv[1]))
