"""Run explicit CPU or configured GPU checks; never grants Owner acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_KEYS = ("REGRAIN_TTS_PYTHON", "REGRAIN_TTS_MODEL",
                "REGRAIN_ALIGNMENT_PYTHON", "REGRAIN_ALIGNMENT_MODEL")


def verify_candidate(wheel: Path, manifest_path: Path) -> None:
    """Bind installed CPU checks to the current clean source and exact wheel."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("project") != "regrain"):
        raise ValueError("unsupported Regrain candidate manifest")
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Full/Owner candidate checks require a clean committed checkout")
    if manifest.get("source_commit") != git("rev-parse", "HEAD"):
        raise ValueError("candidate source commit differs from HEAD; build a new wheel")
    if manifest.get("source_tree") != git("rev-parse", "HEAD^{tree}"):
        raise ValueError("candidate source tree differs from HEAD")
    record = manifest.get("wheel", {})
    if not isinstance(record, dict):
        raise ValueError("malformed candidate wheel record")
    digest = hashlib.sha256()
    with wheel.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if (record.get("filename") != wheel.name or record.get("bytes") != wheel.stat().st_size
            or record.get("sha256") != digest.hexdigest()):
        raise ValueError("wheel filename, size or SHA256 differs from candidate manifest")


def run(python: str, selection: str, paths: list[str], extra: list[str], env: dict) -> int:
    return subprocess.run(
        [python, "-m", "pytest", "-m", selection, *paths, *extra], cwd=ROOT, env=env,
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("fast", "full", "gpu", "owner"))
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--manifest", type=Path)
    args, extra = parser.parse_known_args()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(ROOT / "src")))
    try:
        if args.profile in {"full", "owner"}:
            if args.wheel is None or args.manifest is None:
                raise ValueError("Full/Owner requires --wheel and --manifest from a new clean build")
            wheel = args.wheel.resolve()
            verify_candidate(wheel, args.manifest.resolve())
            env["REGRAIN_TEST_WHEEL"] = str(wheel)
        if args.profile in {"gpu", "owner"}:
            missing = [key for key in RUNTIME_KEYS if not env.get(key)]
            if missing:
                raise ValueError("GPU was requested; configure " + ", ".join(missing))
        if args.profile == "fast":
            return run(sys.executable, "fast", ["tests"], extra, env)
        if args.profile in {"full", "owner"}:
            code = run(sys.executable, "not gpu", ["tests"], extra, env)
            if code or args.profile == "full":
                return code
        for python, module in (
            (env["REGRAIN_TTS_PYTHON"], "tests/test_tts_integration.py"),
            (env["REGRAIN_ALIGNMENT_PYTHON"], "tests/test_alignment_integration.py"),
            (sys.executable, "tests/test_speech_pipeline_e2e_integration.py"),
        ):
            code = run(python, "gpu", [module], extra, env)
            if code:
                return code
        if args.profile == "owner":
            print("Automated checks passed. Exact installed-wheel production scenarios and Owner review remain required.")
        return 0
    except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
