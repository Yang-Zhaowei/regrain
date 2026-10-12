"""Bounded PCM16 I/O for production assets; no sample-list materialization."""
from __future__ import annotations

import hashlib
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from .postprocess import AudioPostprocessError

BLOCK_FRAMES = 65_536


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    frames: int
    pcm_sha256: str


def _format(handle: wave.Wave_read) -> tuple[int, int]:
    if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getcomptype() != "NONE":
        raise AudioPostprocessError("only uncompressed mono 16-bit PCM WAV is supported")
    rate, frames = handle.getframerate(), handle.getnframes()
    if rate <= 0 or frames <= 0:
        raise AudioPostprocessError("WAV must have positive sample rate and frames")
    return rate, frames


def inspect_wav(path: str | Path) -> WavInfo:
    """Validate every declared PCM frame and hash PCM in fixed-size blocks."""
    return inspect_wav_windows(path, ())[0]


def inspect_wav_windows(path: str | Path, windows: tuple[tuple[int, int], ...]) -> tuple[WavInfo, tuple[str, ...]]:
    """Hash full PCM and ordered, non-overlapping windows in one bounded scan."""
    digest = hashlib.sha256()
    window_digests = [hashlib.sha256() for _ in windows]
    try:
        with wave.open(str(path), "rb") as handle:
            rate, frames = _format(handle)
            previous = 0
            for start, end in windows:
                if not previous <= start < end <= frames:
                    raise AudioPostprocessError("invalid or overlapping PCM windows")
                previous = end
            remaining = frames
            offset, index = 0, 0
            while remaining:
                count = min(remaining, BLOCK_FRAMES)
                raw = handle.readframes(count)
                if len(raw) != count * 2:
                    raise AudioPostprocessError("truncated WAV data; declared frame count exceeds PCM bytes")
                digest.update(raw)
                while index < len(windows):
                    start, end = windows[index]
                    if start >= offset + count:
                        break
                    selected_start, selected_end = max(start, offset), min(end, offset + count)
                    window_digests[index].update(raw[(selected_start - offset) * 2:(selected_end - offset) * 2])
                    if end > offset + count:
                        break
                    index += 1
                offset += count
                remaining -= count
    except (wave.Error, EOFError, struct.error) as exc:
        raise AudioPostprocessError(f"cannot read WAV {path}: {exc}") from exc
    return WavInfo(rate, frames, digest.hexdigest()), tuple(item.hexdigest() for item in window_digests)


def copy_wav_window(source: str | Path, destination: str | Path, *, start_frame: int,
                    end_frame: int, sample_rate: int) -> WavInfo:
    """Copy an exact source PCM window to a canonical WAV without decoding."""
    source, destination = Path(source), Path(destination)
    try:
        if source.resolve() == destination.resolve() or (destination.exists() and source.samefile(destination)):
            raise AudioPostprocessError("WAV window destination must differ from its source")
    except RuntimeError as exc:
        raise AudioPostprocessError(f"cannot resolve WAV window paths: {exc}") from exc
    digest = hashlib.sha256()
    try:
        with wave.open(str(source), "rb") as original:
            rate, frames = _format(original)
            if rate != sample_rate or not 0 <= start_frame < end_frame <= frames:
                raise AudioPostprocessError("source WAV metadata/window changed")
            original.setpos(start_frame)
            # Exclusive creation also closes a destination-alias race between
            # the identity check and opening the file. Production owns fresh paths.
            with destination.open("xb") as destination_file, wave.open(destination_file, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                remaining = end_frame - start_frame
                while remaining:
                    count = min(remaining, BLOCK_FRAMES)
                    raw = original.readframes(count)
                    if len(raw) != count * 2:
                        raise AudioPostprocessError("truncated source WAV window")
                    output.writeframesraw(raw)
                    digest.update(raw)
                    remaining -= count
    except (wave.Error, EOFError, struct.error) as exc:
        raise AudioPostprocessError(f"cannot copy WAV window: {exc}") from exc
    return WavInfo(rate, end_frame - start_frame, digest.hexdigest())


def same_pcm(first: str | Path, second: str | Path) -> bool:
    """Compare exact format/frame count and decoded PCM, independent of headers."""
    return inspect_wav(first) == inspect_wav(second)
