"""Explicit, immutable speech revisions and legacy run import; no model calls."""
from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath

from .artifacts import (
    ArtifactError, SpeechUnit, create_unit, load_unit, read_json,
    sha256_file, snapshot_unit, write_json,
)
from .audio_assembly import _CleanedSegment, assemble_audio
from .errors import RenderError
from .postprocess import read_wav
from .render import RenderResult, SegmentResult, _safe_name


def relative_path(value: object, base: Path) -> Path:
    """Portable authored references may cross runs, but cannot be host/URI paths."""
    if not isinstance(value, str) or not value.strip():
        raise ArtifactError("artifact/audio path must be a non-empty relative path")
    windows = PureWindowsPath(value)
    portable = value.replace("\\", "/")
    if windows.drive or windows.root or portable.startswith("/") or ":" in portable:
        raise ArtifactError("artifact/audio path must be relative, without drive or URI")
    return (base / portable).resolve()


def require_object(value: object, keys: set[str], required: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ArtifactError(f"{label} must be an object")
    if set(value) - keys or required - set(value):
        raise ArtifactError(f"{label} fields: unknown {sorted(set(value) - keys)}, missing {sorted(required - set(value))}")
    return value


def integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ArtifactError(f"{label} must be an integer >= {minimum}")
    return value


def string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactError(f"{label} must be a non-empty string")
    return value


def _reference(path: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()
    except ValueError as exc:
        raise ArtifactError("portable references require assets and request on the same filesystem drive") from exc


def load_assembly_plan(path: str | Path, *, _data=None) -> list[tuple[str, SpeechUnit, int]]:
    request = Path(path)
    data = require_object(read_json(request) if _data is None else _data, {"schema_version", "units"}, {"schema_version", "units"}, "assembly")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ArtifactError("unsupported assembly schema_version")
    if not isinstance(data["units"], list) or not data["units"]:
        raise ArtifactError("assembly units must be a non-empty array")
    output, ids = [], set()
    for item in data["units"]:
        item = require_object(item, {"id", "artifact", "sha256", "pause_after_ms"}, {"id", "artifact", "sha256"}, "assembly unit")
        identity = string(item["id"], "unit id")
        if identity in ids:
            raise ArtifactError(f"duplicate unit id: {identity}")
        ids.add(identity)
        pin = item["sha256"]
        if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin):
            raise ArtifactError("unit sha256 must be a lowercase SHA256 digest")
        pause = integer(item.get("pause_after_ms", 0), "pause_after_ms")
        unit = load_unit(relative_path(item["artifact"], request.parent), expected_sha256=pin)
        output.append((identity, unit, pause))
    if len({unit.sample_rate for _, unit, _ in output}) != 1:
        raise ArtifactError("sample rate mismatch; convert and align replacement audio explicitly")
    return output


def _run_entries(run: Path) -> tuple[dict, list[dict]]:
    report = read_json(run / "report.json")
    request = read_json(run / "request.json")
    if not isinstance(report, dict) or not isinstance(request, dict):
        raise ArtifactError("run report/request must be objects")
    entries = report.get("segments")
    segments = request.get("segments", request.get("units"))
    if not isinstance(entries, list) or not isinstance(segments, list) or not segments:
        raise ArtifactError("run has no segment records")
    if any(not isinstance(item, dict) for item in entries + segments):
        raise ArtifactError("run segment records must be objects")
    ids = [string(item.get("id"), "request segment id") for item in segments]
    if len(set(ids)) != len(ids) or [item.get("segment_id") for item in entries] != ids:
        raise ArtifactError("run segment order/identity does not match request")
    return request, entries


def create_assembly_plan(run_dir: str | Path, output_path: str | Path) -> Path:
    """Export the complete requested order with manifest pins; never overwrite."""
    run, destination = Path(run_dir).resolve(), Path(output_path)
    if destination.exists():
        raise ArtifactError("assembly plan output already exists")
    request, entries = _run_entries(run)
    units = []
    for segment, entry in zip(request.get("segments", request.get("units", [])), entries):
        if entry.get("status") != "ok" or not entry.get("unit_manifest"):
            raise ArtifactError(f"segment {segment['id']!r} has no reusable Unit; import a legacy run or make an explicit repair plan")
        manifest = relative_path(entry["unit_manifest"], run)
        if not manifest.is_relative_to(run):
            raise ArtifactError("run unit_manifest escapes run directory")
        pin = string(entry.get("unit_sha256"), "run unit_sha256")
        unit = load_unit(manifest, expected_sha256=pin)
        if ("text" in segment and unit.text != segment["text"]) or ("language" in request and unit.language != request["language"]):
            raise ArtifactError("run transcript/language does not match Unit")
        units.append({"id": segment["id"], "artifact": _reference(manifest, destination.parent), "sha256": unit.manifest_sha256,
                      "pause_after_ms": integer(segment.get("pause_after_ms", 0), "pause_after_ms")})
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation keeps plans, including plans concurrent with this export, immutable.
    import json
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"schema_version": 1, "units": units}, ensure_ascii=False, indent=2) + "\n")
    return destination


def assemble_speech(plan_path: str | Path, output_dir: str | Path) -> RenderResult:
    """Snapshot pinned Units, change pauses/order, produce the standard Final."""
    run = Path(output_dir).resolve()
    if run.exists():
        raise RenderError("output directory already exists; refusing to overwrite")
    try:
        data = read_json(plan_path)
        units = load_assembly_plan(plan_path, _data=data)
    except (ArtifactError, OSError, ValueError) as exc:
        raise RenderError(str(exc)) from exc
    run.mkdir(parents=True)
    report = {"status": "running", "workflow": "assembly", "schema_version": 1,
              "completed_stages": [], "segments": [], "artifacts": {}}
    try:
        write_json(run / "request.json", data)
        write_json(run / "report.json", report)
        cleaned = []
        for order, (identity, source, pause) in enumerate(units, 1):
            unit = snapshot_unit(source, run / "units" / f"{order:04d}")
            cleaned.append(_CleanedSegment(identity, f"{order:04d}", unit.samples, unit.sample_rate, unit.frames, pause, unit.local_captions))
            report["segments"].append({"segment_id": identity, "text": unit.text, "status": "ok",
                "unit_manifest": unit.manifest_path.relative_to(run).as_posix(), "unit_sha256": unit.manifest_sha256,
                "source": {"request_unit_index": order - 1, "sha256": source.manifest_sha256,
                           "content_id": source.content_id}})
        report["completed_stages"].append("snapshot")
        final = assemble_audio(cleaned, run)
        report.update(status="complete", artifacts={"wav_path": "final/final.wav", "srt_path": "final/final.srt", "timeline_path": "final/timeline.json"})
        report["completed_stages"].append("assemble")
        write_json(run / "report.json", report)
        (final / ".complete").write_text("complete", encoding="utf-8")
        return RenderResult("complete", run, run / "report.json", final / "final.wav", final / "final.srt", final / "timeline.json",
                            [SegmentResult(identity, "ok") for identity, _, _ in units])
    except Exception as exc:
        try:
            (run / "final" / ".complete").unlink(missing_ok=True)
        except (OSError, ArtifactError):
            pass
        report.update(status="failed", error=str(exc))
        try:
            write_json(run / "report.json", report)
        except (OSError, ArtifactError):
            pass
        raise RenderError(f"assembly failed: {exc}", run_dir=run) from exc


def import_render_run(run_dir: str | Path, output_dir: str | Path) -> Path:
    """Upgrade a completed v0.1 render into verified Units without altering it.

    Legacy sources have no historical integrity hashes. Hashes identify the bytes
    observed at import, and do not retrospectively establish their authenticity.
    """
    source, target = Path(run_dir).resolve(), Path(output_dir).resolve()
    if target.exists():
        raise ArtifactError("import output directory already exists")
    request, entries = _run_entries(source)
    if "segments" not in request or "language" not in request:
        raise ArtifactError("legacy import requires a render request; use speech plan for an assembly run")
    old_report = read_json(source / "report.json")
    if old_report.get("status") != "complete" or (source / "final" / ".complete").read_text(encoding="utf-8") != "complete":
        raise ArtifactError("legacy import requires complete report and completion marker")
    candidates = []
    for seg, entry in zip(request["segments"], entries):
        if entry.get("status") != "ok" or entry.get("text") != seg.get("text"):
            raise ArtifactError("legacy segment status/transcript mismatch")
        name = _safe_name(seg["id"])
        audio = source / "segments" / (name + ".cleaned.wav")
        alignment = source / "segments" / (name + ".alignment.adjusted.json")
        candidates.append((seg, audio, alignment))
    target.mkdir(parents=True)
    report = {"schema_version": 1, "status": "running", "workflow": "legacy_import", "segments": []}
    try:
        write_json(target / "request.json", request)
        write_json(target / "report.json", report)
        cleaned = []
        for index, (seg, audio, alignment) in enumerate(candidates, 1):
            unit = create_unit(target / "units" / f"{index:04d}", audio_path=audio, alignment_path=alignment,
                text=seg["text"], language=request["language"], provenance={"workflow": "legacy_import",
                    "source_request_sha256": sha256_file(source / "request.json"), "source_report_sha256": sha256_file(source / "report.json"),
                    "segment_id": seg["id"], "integrity_origin": "bytes_observed_at_import"})
            report["segments"].append({"segment_id": seg["id"], "text": seg["text"], "status": "ok",
                "unit_manifest": unit.manifest_path.relative_to(target).as_posix(), "unit_sha256": unit.manifest_sha256})
            cleaned.append(_CleanedSegment(seg["id"], f"{index:04d}", unit.samples, unit.sample_rate, unit.frames,
                integer(seg.get("pause_after_ms", 0), "pause_after_ms"), unit.local_captions))
            write_json(target / "report.json", report)
        final = assemble_audio(cleaned, target)
        # Reconstruct the legacy Final to bind observed Units to that episode.
        # Legacy hashes start at import; successful replay proves consistency.
        if read_wav(source / "final" / "final.wav") != read_wav(final / "final.wav"):
            raise ArtifactError("legacy Final WAV does not match its cleaned Units")
        if (source / "final" / "final.srt").read_text(encoding="utf-8") != (final / "final.srt").read_text(encoding="utf-8"):
            raise ArtifactError("legacy Final SRT does not match its Units/transcript")
        original_timeline = read_json(source / "final" / "timeline.json")
        rebuilt_timeline = read_json(final / "timeline.json")
        if any(original_timeline.get(key) != rebuilt_timeline[key]
               for key in ("sample_rate", "total_frames", "segments")):
            raise ArtifactError("legacy Final timeline does not match its Units")
        report["status"] = "complete"
        report["artifacts"] = {"wav_path": "final/final.wav", "srt_path": "final/final.srt", "timeline_path": "final/timeline.json"}
        write_json(target / "report.json", report)
        (final / ".complete").write_text("complete", encoding="utf-8")
        return target
    except Exception as exc:
        try:
            (target / "final" / ".complete").unlink(missing_ok=True)
        except OSError:
            pass
        report.update(status="failed", error=str(exc))
        try:
            write_json(target / "report.json", report)
        except (OSError, ArtifactError):
            pass
        raise RenderError(f"legacy import failed: {exc}", run_dir=target) from exc
