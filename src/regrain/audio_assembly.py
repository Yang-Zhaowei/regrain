"""Model-free frame-exact audio assembly shared by all speech workflows."""
from __future__ import annotations
import json
import hashlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from .captions import Caption, render_srt
from .postprocess import read_wav, write_wav
from .errors import RenderError

_TIMING_TOLERANCE = 1e-6
_SRT_MS_TOLERANCE = 0.5

@dataclass(frozen=True)
class _CleanedSegment:
    """A segment that passed every stage and can participate in assembly."""

    segment_id: str
    safe_name: str
    samples: list[int]
    sample_rate: int
    frames: int
    pause_after_ms: int
    local_captions: list[Caption]


def assemble_audio(cleaned: list[_CleanedSegment], run_dir: Path, *, publish=None) -> Path:
    """Write verified final files. Caller writes its report and marker last."""
    if not cleaned:
        raise RenderError("assembly requires at least one unit", run_dir=run_dir)
    publish = publish or _publish
    # --- integer-frame accumulation (C7) ------------------------------------
    common_rate = cleaned[0].sample_rate
    for seg in cleaned:
        if seg.sample_rate != common_rate:
            raise RenderError(
                f"sample rate mismatch during assembly: "
                f"{seg.segment_id} has {seg.sample_rate} Hz != {common_rate} Hz",
                run_dir=run_dir,
            )

    # Local caption text comes from each segment's own original text; never
    # recombined across input segments.

    total_frames = 0
    all_samples: list[int] = []
    global_captions: list[Caption] = []
    timeline_segments: list[dict] = []
    running = 0  # O_i: cumulative frames before this segment

    for seg in cleaned:
        n_i = seg.frames
        g_i = _half_up_frames(seg.pause_after_ms, common_rate)
        offset_frames = running  # O_i

        for caption in seg.local_captions:
            global_captions.append(
                Caption(
                    start=caption.start + offset_frames / common_rate,
                    end=caption.end + offset_frames / common_rate,
                    text=caption.text,
                )
            )

        all_samples.extend(seg.samples)
        all_samples.extend([0] * g_i)
        running += n_i + g_i

        timeline_segments.append(
            {
                "segment_id": seg.segment_id,
                "start_frame": offset_frames,
                "audio_frames": n_i,
                "pause_after_frames": g_i,
            }
        )

    total_frames = running

    # --- verify the caption timeline before writing anything ----------------
    _verify_caption_timeline(global_captions, total_frames, common_rate, run_dir)

    # --- verify the integer-frame timeline integrity (C7) -------------------
    _verify_timeline_integrity(
        timeline_segments,
        [seg.segment_id for seg in cleaned],
        total_frames,
        run_dir,
    )

    # --- render SRT (only at the very end, existing half-up rules) ----------
    srt_text = render_srt(global_captions)

    # --- generate final products in a staging dir, then re-read & validate --
    staging = run_dir / ".final.staging"
    staging.mkdir(parents=True, exist_ok=True)
    final_wav = staging / "final.wav"
    final_srt = staging / "final.srt"
    final_timeline = staging / "timeline.json"

    write_wav(final_wav, all_samples, common_rate)
    final_srt.write_text(srt_text, encoding="utf-8", newline="\n")
    final_timeline.write_text(
        json.dumps(
            {
                "sample_rate": common_rate,
                "total_frames": total_frames,
                "start_seconds": 0.0,
                "duration_seconds": total_frames / common_rate,
                "segments": timeline_segments,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    _verify_final_products(
        staging,
        common_rate,
        total_frames,
        global_captions,
        [seg.segment_id for seg in cleaned],
    )

    # --- publish final/ products, then complete the run transaction (C6) ---
    # Order is a transaction: publish the artifacts, write the ``complete``
    # report successfully, and only then write the ``.complete`` marker last.
    # A failure at any step leaves no ``.complete`` marker, so a consumer never
    # treats a failed publish/report/marker write as success.
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    expected_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (final_wav, final_srt, final_timeline)}
    publish(final_wav, final_dir / "final.wav")
    publish(final_srt, final_dir / "final.srt")
    publish(final_timeline, final_dir / "timeline.json")

    for name, expected in expected_hashes.items():
        if hashlib.sha256((final_dir / name).read_bytes()).hexdigest() != expected:
            raise RenderError(f"published final product hash mismatch: {name}", run_dir=run_dir)
    _verify_final_products(final_dir, common_rate, total_frames, global_captions,
                           [seg.segment_id for seg in cleaned])

    return final_dir


def _publish(source: Path, destination: Path) -> None:
    """Copy a staged final product into ``final/``."""

    destination.write_bytes(source.read_bytes())


# --- timeline helpers -------------------------------------------------------


def _half_up_frames(pause_after_ms: int, sample_rate: int) -> int:
    """Number of silence frames inserted after a segment (half-up)."""

    return int((Decimal(str(pause_after_ms)) * sample_rate / Decimal(1000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ))


def _verify_caption_timeline(
    captions: list[Caption], total_frames: int, sample_rate: int, run_dir: Path
) -> None:
    """Validate the whole-episode caption timeline before writing.

    Consecutive, non-empty, start before end, non-negative, non-overlapping
    (touching allowed). Millisecond quantization causing a zero-duration caption
    is an explicit failure. Every emitted boundary must fall inside the final
    WAV, allowing the 0.5 ms SRT rounding tolerance against the precise audio
    edge -- but never accumulated drift.
    """

    if not captions:
        raise RenderError("assembly produced no captions", run_dir=run_dir)

    final_duration = total_frames / sample_rate
    previous_end = -1.0
    for index, caption in enumerate(captions):
        if not caption.text:
            raise RenderError(
                f"caption {index + 1} has empty text after assembly", run_dir=run_dir
            )
        if caption.start < 0 or caption.end < 0:
            raise RenderError(
                f"caption {index + 1} has negative timing", run_dir=run_dir
            )
        if not (caption.start < caption.end):
            raise RenderError(
                f"caption {index + 1} has start >= end", run_dir=run_dir
            )
        # Zero duration from millisecond quantization is an explicit failure.
        start_ms = _caption_ms(caption.start)
        end_ms = _caption_ms(caption.end)
        if end_ms - start_ms <= 0:
            raise RenderError(
                f"caption {index + 1} has zero or negative duration after "
                f"millisecond quantization",
                run_dir=run_dir,
            )
        if caption.start < previous_end - _TIMING_TOLERANCE:
            raise RenderError(
                f"caption {index + 1} overlaps the previous caption", run_dir=run_dir
            )
        previous_end = caption.end
        emitted_end_ms = end_ms
        if emitted_end_ms > final_duration * 1000 + _SRT_MS_TOLERANCE + 1e-6:
            raise RenderError(
                f"caption {index + 1} ends at {emitted_end_ms} ms, past the final "
                f"WAV duration {final_duration * 1000} ms",
                run_dir=run_dir,
            )


def _verify_timeline_integrity(
    timeline_segments: object,
    segment_ids: list[str],
    total_frames: int,
    run_dir: Path,
) -> None:
    """Verify the integer-frame timeline accumulation against the source of truth (C7).

    ``segment_ids`` is the ordered list of input segment ids; ``timeline_segments``
    is the ``segments`` array from ``timeline.json``. Each entry must carry the
    four required integer fields, appear in the input order, and have a
    ``start_frame`` equal to the cumulative ``audio_frames + pause_after_frames``
    of the preceding segments. The accumulated total must equal ``total_frames``.
    """

    if not isinstance(timeline_segments, list):
        raise RenderError(
            "timeline is missing its per-segment entries", run_dir=run_dir
        )
    if len(timeline_segments) != len(segment_ids):
        raise RenderError(
            "timeline segment count does not match the input segments",
            run_dir=run_dir,
        )
    running = 0
    for entry, expected_id in zip(timeline_segments, segment_ids):
        for key in ("segment_id", "start_frame", "audio_frames", "pause_after_frames"):
            if key not in entry:
                raise RenderError(
                    f"timeline segment entry missing {key!r}", run_dir=run_dir
                )
        for key in ("start_frame", "audio_frames", "pause_after_frames"):
            # ``bool`` is an ``int`` subclass; reject it so a frame count can
            # never be silently stored as ``True``/``False``.
            if not isinstance(entry[key], int) or isinstance(entry[key], bool):
                raise RenderError(
                    f"timeline {key} must be an integer frame count",
                    run_dir=run_dir,
                )
        if entry["segment_id"] != expected_id:
            raise RenderError(
                "timeline segment order does not match the input segments",
                run_dir=run_dir,
            )
        if entry["start_frame"] != running:
            raise RenderError(
                f"timeline segment {expected_id!r} start_frame "
                f"{entry['start_frame']} does not match the accumulated "
                f"{running} (integer-frame offset)",
                run_dir=run_dir,
            )
        running += entry["audio_frames"] + entry["pause_after_frames"]
    if running != total_frames:
        raise RenderError(
            f"timeline total_frames {total_frames} does not match the "
            f"accumulated {running}",
            run_dir=run_dir,
        )


def _caption_ms(seconds: float) -> int:
    """Render seconds to an SRT millisecond integer (independent of the renderer)."""

    return int((Decimal(str(seconds)) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _verify_final_products(
    final_dir: Path,
    sample_rate: int,
    total_frames: int,
    captions: list[Caption],
    segment_ids: list[str],
) -> None:
    """Re-read the published final WAV/SRT/timeline and verify them against disk."""

    # --- WAV: frame count / rate / format ---
    samples, rate, frames = read_wav(final_dir / "final.wav")
    if rate != sample_rate or frames != total_frames or len(samples) != frames:
        raise RenderError(
            "final WAV re-read mismatch: "
            f"rate={rate}, frames={frames}, expected {sample_rate}/{total_frames}",
            run_dir=final_dir.parent,
        )

    # --- SRT: actual file content must equal the rendered document ---
    srt_text = (final_dir / "final.srt").read_text(encoding="utf-8")
    if srt_text != render_srt(captions):
        raise RenderError(
            "final SRT re-read content does not match the compiled captions",
            run_dir=final_dir.parent,
        )

    # --- timeline: re-read and confirm the derived integers ---
    try:
        timeline = json.loads((final_dir / "timeline.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RenderError(f"cannot re-read timeline: {exc}", run_dir=final_dir.parent) from exc

    if timeline.get("sample_rate") != sample_rate:
        raise RenderError(
            "timeline sample_rate does not match the common WAV rate",
            run_dir=final_dir.parent,
        )
    if timeline.get("total_frames") != total_frames:
        raise RenderError(
            "timeline total_frames does not match the assembled frame count",
            run_dir=final_dir.parent,
        )
    timeline_segments = timeline.get("segments")
    # Re-verify the integer-frame accumulation against the re-read file (C7).
    _verify_timeline_integrity(
        timeline_segments, segment_ids, total_frames, final_dir.parent
    )
    # Second values, if present, must be derived from the integer frames, never
    # from independently rounded millisecond accumulations.
    if "duration_seconds" in timeline:
        expected_duration = total_frames / sample_rate
        if abs(timeline["duration_seconds"] - expected_duration) > _TIMING_TOLERANCE:
            raise RenderError(
                "timeline duration_seconds is not derived from integer frames",
                run_dir=final_dir.parent,
            )
    if "start_seconds" in timeline and timeline["start_seconds"] != 0.0:
        raise RenderError(
            "timeline start_seconds is not zero",
            run_dir=final_dir.parent,
        )
