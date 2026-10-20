#!/usr/bin/env python3
"""Build a standalone Apple Silicon runtime for the future hybrid UXP plugin.

Uses existing build dependencies. It never installs packages, launches an app,
downloads Adobe SDKs, or depends on an installed ReSync.app.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile


REPOSITORY = Path(__file__).resolve().parents[1]
WORKER_NAME = "resync-premiere-worker"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dependency_info(python: Path) -> dict:
    # Select the binary in the installed wheel directly. get_ffmpeg_exe()
    # intentionally accepts environment/system overrides, which this runtime
    # must not accidentally embed instead of the build dependency.
    script = r'''
import importlib.metadata, json, os, pathlib, platform, sys, sysconfig
import imageio_ffmpeg, numpy, PyInstaller
folder = pathlib.Path(imageio_ffmpeg.__file__).resolve().parent / 'binaries'
binaries = sorted(str(p) for p in folder.glob('ffmpeg-*')
                  if p.is_file() and os.access(p, os.X_OK))
if len(binaries) != 1:
    raise RuntimeError('Expected exactly one FFmpeg executable in imageio-ffmpeg wheel')
licenses = []
for package in ('numpy', 'pyinstaller', 'imageio-ffmpeg'):
    distribution = importlib.metadata.distribution(package)
    for relative in distribution.files or []:
        name = pathlib.PurePosixPath(str(relative))
        if name.name.lower().startswith(('license', 'copying', 'notice')) and '..' not in name.parts:
            path = distribution.locate_file(relative)
            if path.is_file():
                licenses.append({'package': package, 'path': str(path), 'relative': str(name)})
python_license = pathlib.Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt'
if python_license.is_file():
    licenses.append({'package': 'python', 'path': str(python_license), 'relative': 'LICENSE.txt'})
print(json.dumps({'platform': sys.platform, 'architecture': platform.machine(),
                  'python_version': platform.python_version(), 'numpy_version': numpy.__version__,
                  'pyinstaller_version': PyInstaller.__version__,
                  'imageio_ffmpeg_version': importlib.metadata.version('imageio-ffmpeg'),
                  'ffmpeg': binaries[0], 'license_files': licenses}))
'''
    output = subprocess.check_output([str(python), "-c", script], text=True)
    info = json.loads(output)
    if info["platform"] != "darwin" or info["architecture"] != "arm64":
        raise RuntimeError("Esta primeira etapa compila somente em macOS Apple Silicon (arm64).")
    return info


def publish_runtime(built: Path, destination: Path, replace: bool, scratch: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    if destination.exists():
        if not replace:
            raise FileExistsError(f"Runtime já existe: {destination}. Use --replace para recompilar.")
        marker = destination / "runtime.json"
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("A pasta existente não é um runtime ReSync gerado por este builder.") from exc
        if data.get("schema_version") != 1 or data.get("worker_relative_path") != f"{WORKER_NAME}/{WORKER_NAME}":
            raise ValueError("A pasta existente não é um runtime ReSync reconhecido.")
        previous = scratch / "previous-runtime"
        destination.rename(previous)
    try:
        built.rename(destination)
    except OSError:
        if previous is not None:
            previous.rename(destination)
        raise


def build_runtime(stage: Path, python: Path, engine_version: str, replace=False, smoke=False) -> Path:
    info = dependency_info(python)
    ffmpeg = Path(info.pop("ffmpeg"))
    license_files = info.pop("license_files")
    probe = subprocess.run([str(ffmpeg), "-version"], text=True, capture_output=True,
                           timeout=15, check=True)
    ffmpeg_version = probe.stdout.splitlines()[0]
    if not ffmpeg_version.startswith("ffmpeg version "):
        raise RuntimeError("O FFmpeg da dependência não respondeu corretamente.")
    stage = stage.resolve()
    stage.parent.mkdir(parents=True, exist_ok=True)
    destination = stage / "mac" / "arm64" / "runtime"
    if destination.exists() and not replace:
        raise FileExistsError(f"Runtime já existe: {destination}. Use --replace para recompilar.")
    source = REPOSITORY / "source"
    with tempfile.TemporaryDirectory(prefix="resync-premiere-build-", dir=stage.parent) as temporary:
        scratch = Path(temporary)
        command = [str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                   "--name", WORKER_NAME, "--distpath", str(scratch / "dist"),
                   "--workpath", str(scratch / "build"), "--specpath", str(scratch / "spec"),
                   "--paths", str(source), str(source / "premiere_worker.py")]
        # PyInstaller logs to stderr; stdout remains machine-readable at exit.
        build_env = os.environ.copy()
        build_env["PYINSTALLER_CONFIG_DIR"] = str(scratch / "pyinstaller-cache")
        subprocess.run(command, cwd=REPOSITORY, check=True, stdout=sys.stderr, env=build_env)
        runtime = scratch / "runtime"
        runtime.mkdir()
        shutil.copytree(scratch / "dist" / WORKER_NAME, runtime / WORKER_NAME)
        (runtime / "bin").mkdir()
        bundled_ffmpeg = runtime / "bin" / "ffmpeg"
        shutil.copy2(ffmpeg, bundled_ffmpeg)
        bundled_ffmpeg.chmod(0o755)
        licenses = runtime / "licenses"
        copied_notices = []
        for item in license_files:
            relative = Path(item["relative"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Caminho inválido nos avisos da dependência de build.")
            target = licenses / item["package"] / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item["path"], target)
            copied_notices.append(str(target.relative_to(runtime)))
        ffmpeg_notices = licenses / "ffmpeg"
        ffmpeg_notices.mkdir(parents=True, exist_ok=True)
        (ffmpeg_notices / "FFMPEG-VERSION.txt").write_text(probe.stdout + probe.stderr, encoding="utf-8")
        license_probe = subprocess.run([str(ffmpeg), "-L"], text=True, capture_output=True,
                                       timeout=15, check=True)
        (ffmpeg_notices / "FFMPEG-L.txt").write_text(license_probe.stdout + license_probe.stderr,
                                                    encoding="utf-8")
        copied_notices += ["licenses/ffmpeg/FFMPEG-VERSION.txt", "licenses/ffmpeg/FFMPEG-L.txt"]
        (licenses / "README.md").write_text(
            "# Third-party provenance for the ReSync Premiere runtime\n\n"
            "Notices are copied unchanged from the locally installed Python, NumPy, PyInstaller "
            "and imageio-ffmpeg distributions when present. FFmpeg's own version, configuration "
            "and license-report output are included in ffmpeg/. The wheel's wrapper license "
            "must not be treated as the license of the FFmpeg executable.\n\n"
            "This prototype does not claim complete redistribution clearance. Before public "
            "distribution, review the exact FFmpeg build and its dependencies, complete any "
            "missing license/source materials, and sign/notarize all required native components. "
            "No Adobe Hybrid SDK is included in this runtime.\n", encoding="utf-8")
        metadata = {
            "schema_version": 1,
            "worker_cli_protocol": 1,
            "job_protocol": 1,
            "engine_version": engine_version,
            **info,
            "worker_relative_path": f"{WORKER_NAME}/{WORKER_NAME}",
            "ffmpeg_relative_path": "bin/ffmpeg",
            "ffmpeg_sha256": sha256(bundled_ffmpeg),
            "worker_sha256": sha256(runtime / WORKER_NAME / WORKER_NAME),
            "ffmpeg_version": ffmpeg_version,
            "third_party_notices": sorted(copied_notices),
            "source_sha256": {name: sha256(source / name) for name in
                              ("engine.py", "sync_job.py", "premiere_worker.py")},
        }
        (runtime / "runtime.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
        if smoke:
            # Verify before publishing. A failed candidate must never replace
            # a previously working runtime, including with --replace.
            subprocess.run([str(python), str(REPOSITORY / "build" / "test_premiere_runtime.py"),
                            str(runtime)], check=True)
        publish_runtime(runtime, destination, replace, scratch)
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", "--output", dest="stage", type=Path, required=True,
                        help="Pasta de staging do plugin")
    parser.add_argument("--python", type=Path, default=Path(sys.executable),
                        help="Python de build com NumPy, PyInstaller e imageio-ffmpeg já instalados")
    default_version = plistlib.loads((REPOSITORY / "build" / "Info.plist").read_bytes())["CFBundleShortVersionString"]
    parser.add_argument("--engine-version", default=default_version)
    parser.add_argument("--replace", action="store_true", help="Substitui somente um runtime gerado anteriormente")
    parser.add_argument("--smoke", action="store_true", help="Executa os fixtures reais no worker congelado")
    args = parser.parse_args(argv)
    # Preserve the venv interpreter symlink: resolving it to its base Python
    # would lose access to the already-installed build dependencies.
    python = args.python.absolute()
    runtime = build_runtime(args.stage, python, args.engine_version, args.replace, args.smoke)
    print(json.dumps({"runtime": str(runtime), "engine_version": args.engine_version,
                      "smoke_passed": args.smoke}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
