"""Forced alignment of supplied audio and exact transcripts, using speech Units."""
from __future__ import annotations

from dataclasses import dataclass, replace
import struct
from pathlib import Path

from .alignment import fault_is_io_error, validate_alignment
from .artifacts import ArtifactError, create_unit, read_json, sha256_file, write_json
from .audio_assembly import _CleanedSegment, assemble_audio, _publish_complete_marker
from .captions import CaptionError, check_alignment_matches_text, has_alignable_content
from .errors import RenderError
from .postprocess import AudioPostprocessError, postprocess_speech, read_wav
from .production import integer, relative_path, require_object, string
from .render import RenderResult, SegmentResult, _make_align_task
from .wav_io import copy_wav_window, inspect_wav, inspect_wav_windows

# Upstream ForcedAligner advertises up to five minutes. This is an input bound,
# not evidence of accepted GPU performance or transcription accuracy.
MAX_ALIGNMENT_SECONDS = 300


@dataclass(frozen=True)
class AudioWindow:
    id: str
    text: str
    audio: Path
    source_sha256: str
    sample_rate: int
    samples: list[int] | None
    start_frame: int
    end_frame: int
    pause_after_ms: int
    pcm_sha256: str | None = None

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


def load_audio_request(path: str | Path, *, _metadata_only: bool = False) -> tuple[dict, list[AudioWindow]]:
    request = Path(path)
    data = require_object(read_json(request), {"schema_version", "language", "processing", "segments"},
                          {"schema_version", "language", "segments"}, "audio alignment")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ArtifactError("unsupported audio alignment schema_version")
    string(data["language"], "language")
    if data.get("processing", "preserve") not in ("preserve", "trim"):
        raise ArtifactError("processing must be preserve or trim")
    if not isinstance(data["segments"], list) or not data["segments"]:
        raise ArtifactError("segments must be a non-empty array")
    windows, identities, sources, previous = [], set(), {}, {}
    for item in data["segments"]:
        seg = require_object(item, {"id", "audio", "text", "start_frame", "end_frame", "pause_after_ms"},
                             {"id", "audio", "text"}, "audio segment")
        identity, text = string(seg["id"], "segment id"), string(seg["text"], "text")
        if identity in identities:
            raise ArtifactError(f"duplicate segment id: {identity}")
        identities.add(identity)
        if not has_alignable_content(text):
            raise ArtifactError("text must contain alignable content")
        audio = relative_path(seg["audio"], request.parent)
        if audio not in sources:
            before = sha256_file(audio)
            try:
                if _metadata_only:
                    info = inspect_wav(audio)
                    samples, rate, frames = None, info.sample_rate, info.frames
                else:
                    samples, rate, frames = read_wav(audio)
            except (AudioPostprocessError, EOFError, struct.error) as exc:
                raise ArtifactError(f"input WAV is invalid: {exc}") from exc
            if rate <= 0 or frames <= 0:
                raise ArtifactError("audio must have positive sample rate and frames")
            if before != sha256_file(audio):
                raise ArtifactError("audio changed during preflight")
            sources[audio] = (samples, rate, frames, before, info.pcm_sha256 if _metadata_only else None)
        samples, rate, frames, digest, _ = sources[audio]
        if ("start_frame" in seg) != ("end_frame" in seg):
            raise ArtifactError("start_frame and end_frame must be supplied together")
        start = integer(seg.get("start_frame", 0), "start_frame")
        end = integer(seg.get("end_frame", frames), "end_frame", minimum=1)
        if start >= end or end > frames:
            raise ArtifactError("audio window must be non-empty and inside source WAV")
        if end - start > MAX_ALIGNMENT_SECONDS * rate:
            raise ArtifactError("audio window exceeds 300 seconds; supply explicit frame windows and matching transcripts")
        if audio in previous and start < previous[audio]:
            raise ArtifactError("windows of the same recording must be ordered and non-overlapping")
        previous[audio] = end
        windows.append(AudioWindow(identity, text, audio, digest, rate, samples[start:end] if samples is not None else None, start, end,
                                   integer(seg.get("pause_after_ms", 0), "pause_after_ms")))
    if len({win.sample_rate for win in windows}) != 1:
        raise ArtifactError("sample rate mismatch; convert recordings explicitly before alignment")
    for source, (_, rate, frames, source_digest, pcm_digest) in sources.items():
        indices = [index for index, win in enumerate(windows) if win.audio == source]
        ranges = tuple((windows[index].start_frame, windows[index].end_frame) for index in indices)
        try:
            info, digests = inspect_wav_windows(source, ranges)
        except AudioPostprocessError as exc:
            raise ArtifactError(f"input WAV is invalid: {exc}") from exc
        if (info.sample_rate, info.frames) != (rate, frames) or (pcm_digest is not None and info.pcm_sha256 != pcm_digest):
            raise ArtifactError("audio PCM changed during preflight")
        if sha256_file(source) != source_digest:
            raise ArtifactError("audio changed during preflight")
        for index, window_digest in zip(indices, digests):
            windows[index] = replace(windows[index], pcm_sha256=window_digest)
    return data, windows


def align_speech(request_path: str | Path, output_dir: str | Path, *, alignment_python: str,
                 alignment_model: str, device: str = "cuda:0", _align_task=None, _prepared=None) -> RenderResult:
    """Snapshot recordings, align each explicit window once, and publish Final.

    No TTS, ASR, text inference, automatic slicing of transcripts, resampling,
    or estimated timestamps. Complete Units survive an incomplete or failed run.
    """
    run = Path(output_dir).resolve()
    if run.exists():
        raise RenderError("output directory already exists; refusing to overwrite")
    try:
        data, windows = _prepared if _prepared is not None else load_audio_request(request_path, _metadata_only=True)
    except (OSError, ValueError) as exc:
        raise RenderError(str(exc)) from exc
    run.mkdir(parents=True)
    report = {"schema_version": 1, "workflow": "external_alignment", "status": "running",
              "config": {"alignment_python": str(alignment_python), "alignment_model": str(alignment_model), "device": device},
              "segments": [{"segment_id": win.id, "text": win.text, "status": "pending"} for win in windows],
              "completed_stages": [], "artifacts": {}}
    try:
        write_json(run / "request.json", data)
        write_json(run / "report.json", report)
        segments_dir = run / "segments"
        segments_dir.mkdir()
        segments = []
        snapshot_hashes = {}
        source_hashes = {win.audio: win.source_sha256 for win in windows}
        for source, digest in source_hashes.items():
            if sha256_file(source) != digest:
                raise ArtifactError("source audio changed since preflight")
        for index, win in enumerate(windows, 1):
            name = f"{index:04d}"
            copied = copy_wav_window(win.audio, segments_dir / (name + ".wav"), start_frame=win.start_frame,
                                    end_frame=win.end_frame, sample_rate=win.sample_rate)
            if copied.pcm_sha256 != win.pcm_sha256:
                raise ArtifactError(f"source PCM changed while snapshotting window: {win.id}")
            snapshot_hashes[win.id] = sha256_file(segments_dir / (name + ".wav"))
            segments.append({"id": win.id, "safe_name": name, "text": win.text})
        for source, digest in source_hashes.items():
            if sha256_file(source) != digest:
                raise ArtifactError("source audio changed while snapshotting windows")
        report["completed_stages"].append("snapshot")
        write_json(run / "report.json", report)
        task = _align_task or _make_align_task(alignment_python, alignment_model, device)
        result = task({"run_dir": str(run), "language": data["language"], "device": device, "segments": segments})
        entries = result.get("segments", [])
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise ArtifactError("alignment returned malformed segment results")
        expected = {win.id for win in windows}
        by_id = {item.get("segment_id"): item for item in entries}
        if len(by_id) != len(entries) or set(by_id) - expected:
            raise ArtifactError("alignment returned duplicate or unexpected segment results")
        cleaned = []
        for index, (win, entry) in enumerate(zip(windows, report["segments"]), 1):
            item = by_id.get(win.id)
            if item is None:
                if result.get("runtime_error"):
                    continue  # preserve verified successful prefix before stopping
                raise ArtifactError(f"alignment returned no result for {win.id}")
            if item.get("status") == "alignment_failed":
                entry.update(status="alignment_failed", stage="alignment", reason=item.get("reason", "alignment validation failed"))
                write_json(run / "report.json", report)
                continue
            if item.get("status") != "ok":
                raise ArtifactError(f"alignment returned unexpected status for {win.id}: {item}")
            name = f"{index:04d}"
            audio = segments_dir / (name + ".wav")
            if sha256_file(audio) != snapshot_hashes[win.id]:
                raise ArtifactError(f"alignment stage changed immutable audio snapshot: {win.id}")
            alignment = segments_dir / (name + ".alignment.raw.json")
            try:
                raw = read_json(alignment)
                if not isinstance(raw, list):
                    raise ArtifactError("alignment must be an array")
                tokens = validate_alignment(raw, sample_rate=win.sample_rate, frames=win.frames)
                check_alignment_matches_text(win.text, tokens)
                if data.get("processing", "preserve") == "trim":
                    audio = segments_dir / (name + ".cleaned.wav")
                    adjusted = segments_dir / (name + ".alignment.adjusted.json")
                    postprocess_speech(segments_dir / (name + ".wav"), alignment, audio, adjusted)
                    alignment = adjusted
                unit = create_unit(run / "units" / name, audio_path=audio, alignment_path=alignment,
                    text=win.text, language=data["language"], provenance={"workflow": "external_alignment",
                        "source_audio_sha256": win.source_sha256, "source_start_frame": win.start_frame,
                        "source_end_frame": win.end_frame, "source_sample_rate": win.sample_rate,
                        "processing": data.get("processing", "preserve"), "segment_id": win.id,
                        "window_audio_sha256": snapshot_hashes[win.id],
                        "raw_alignment_sha256": sha256_file(segments_dir / (name + ".alignment.raw.json"))}, _materialize_samples=False)
                if sha256_file(segments_dir / (name + ".wav")) != snapshot_hashes[win.id]:
                    (unit.manifest_path.parent / ".complete").unlink(missing_ok=True)
                    raise RenderError(f"immutable audio snapshot changed during Unit creation: {win.id}")
            except (ArtifactError, CaptionError, AudioPostprocessError) as exc:
                if fault_is_io_error(exc):
                    raise
                entry.update(status="alignment_failed", stage="unit_validation", reason=str(exc))
                write_json(run / "report.json", report)
                continue
            entry.update(status="ok", unit_manifest=unit.manifest_path.relative_to(run).as_posix(), unit_sha256=unit.manifest_sha256)
            cleaned.append(_CleanedSegment(win.id, name, None, unit.sample_rate, unit.frames, win.pause_after_ms, unit.local_captions,
                                            unit.audio_path, unit.audio_sha256, unit.pcm_sha256))
            write_json(run / "report.json", report)
        if result.get("runtime_error"):
            raise RenderError(f"Alignment runtime failure: {result['runtime_error']}")
        report["completed_stages"].extend(["alignment", "units"])
        if any(entry["status"] != "ok" for entry in report["segments"]):
            report["status"] = "incomplete"
            write_json(run / "report.json", report)
            return RenderResult("incomplete", run, run / "report.json", None, None, None,
                [SegmentResult(entry["segment_id"], entry["status"], entry.get("stage"), entry.get("reason")) for entry in report["segments"]])
        final = assemble_audio(cleaned, run)
        report.update(status="complete", artifacts={"wav_path": "final/final.wav", "srt_path": "final/final.srt", "timeline_path": "final/timeline.json"})
        report["completed_stages"].append("assemble")
        write_json(run / "report.json", report)
        _publish_complete_marker(final)
        return RenderResult("complete", run, run / "report.json", final / "final.wav", final / "final.srt", final / "timeline.json",
                            [SegmentResult(win.id, "ok") for win in windows])
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
        raise RenderError(f"external alignment failed: {exc}", run_dir=run) from exc
