"""Verified, portable speech Units shared by rendering and assembly.

A Unit owns its WAV and precise alignment. Its completion marker authenticates
the exact manifest bytes; the manifest in turn binds the transcript, provenance
and asset hashes. Loading always verifies these relationships. Inputs are copied
into a new directory, never edited in place. No model runtime is imported here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from .alignment import AlignmentRequest, validate_alignment, validate_request, validate_wav
from .captions import (
    AlignedToken, Caption, build_captions, check_alignment_matches_text, format_timestamp,
)
from .postprocess import read_wav

__all__ = [
    "ArtifactError", "SpeechUnit", "create_unit", "load_unit", "snapshot_unit",
    "sha256_file", "write_json", "read_json",
]

_HASH = re.compile(r"[a-f0-9]{64}\Z")


class ArtifactError(ValueError):
    """A speech artifact is invalid, incomplete, changed, or inaccessible."""


@dataclass(frozen=True)
class SpeechUnit:
    """Validated Unit data; captions retain the alignment's full precision."""

    manifest_path: Path
    manifest_sha256: str
    text: str
    language: str
    samples: list[int]
    sample_rate: int
    frames: int
    tokens: list[AlignedToken]
    local_captions: list[Caption]
    provenance: dict[str, Any]
    _audio_path: Path
    _alignment_path: Path
    content_id: str

    @property
    def audio_path(self) -> Path:
        return self._audio_path

    @property
    def alignment_path(self) -> Path:
        return self._alignment_path

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


def _canonical(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ArtifactError(f"artifact data is not portable JSON: {exc}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ArtifactError(f"JSON contains non-finite number {value}")


def _finite_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise ArtifactError(f"JSON contains non-finite number {value}")
    return decoded


def _parse_json(data: bytes, path: Path) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_reject_constant, parse_float=_finite_float,
        )
    except (ValueError, UnicodeError) as exc:
        raise ArtifactError(f"invalid JSON in {path}: {exc}") from exc


def read_json(path: str | Path) -> Any:
    """Read strict UTF-8 JSON, rejecting duplicate keys and NaN/Infinity."""

    source = Path(path)
    try:
        return _parse_json(source.read_bytes(), source)
    except OSError as exc:
        raise ArtifactError(f"cannot read JSON {source}: {exc}") from exc


def _atomic_write(path: Path, data: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise ArtifactError(f"cannot write artifact {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_json(path: str | Path, payload: Any) -> None:
    """Atomically write deterministic UTF-8 JSON in the target directory."""

    _atomic_write(Path(path), _canonical(payload) + b"\n")


def sha256_file(path: str | Path) -> str:
    """Hash exact file bytes without loading the complete file into memory."""

    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ArtifactError(f"cannot hash artifact {path}: {exc}") from exc
    return digest.hexdigest()


def _valid_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ArtifactError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArtifactError(f"{field} must be a positive integer")
    return value


def _confined_asset(root: Path, value: Any, field: str) -> Path:
    # Reject Windows spellings on every host, including drive-relative paths.
    # POSIX relative paths are the serialized cross-platform contract.
    if (
        not isinstance(value, str) or not value or "\\" in value or ":" in value
        or value.startswith("/") or PureWindowsPath(value).drive
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ArtifactError(f"{field} must be a confined relative POSIX path")
    candidate = root.joinpath(*value.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ArtifactError(f"cannot resolve {field}: {exc}") from exc
    if root not in resolved.parents or not resolved.is_file():
        raise ArtifactError(f"{field} escapes the Unit directory or is not a file")
    return candidate


def _validated_data(
    audio_path: Path, alignment_path: Path, text: Any, language: Any,
) -> tuple[list[int], int, int, list[AlignedToken], list[Caption]]:
    try:
        validate_request(AlignmentRequest(audio_path, text, language))
        sample_rate, frames = validate_wav(audio_path)
        samples, decoded_rate, decoded_frames = read_wav(audio_path)
        if (decoded_rate, decoded_frames) != (sample_rate, frames):
            raise ArtifactError("WAV changed while its metadata was read")
        raw_alignment = read_json(alignment_path)
        if not isinstance(raw_alignment, list):
            raise ArtifactError("alignment must be a JSON array")
        tokens = validate_alignment(raw_alignment, sample_rate=sample_rate, frames=frames)
        check_alignment_matches_text(text, tokens)
        captions = build_captions(text, tokens)
        duration = frames / sample_rate
        if not captions or any(
            caption.start < 0 or caption.end <= caption.start
            or caption.end > duration + 1e-6
            or format_timestamp(caption.start) == format_timestamp(caption.end)
            for caption in captions
        ):
            raise ArtifactError("captions are empty, have no usable duration, or are outside the WAV timeline")
        return samples, sample_rate, frames, tokens, captions
    except (ValueError, OSError, EOFError, struct.error) as exc:
        if isinstance(exc, ArtifactError):
            raise
        raise ArtifactError(f"invalid speech Unit inputs: {exc}") from exc


def _load_unit(
    manifest_path: str | Path, *, expected_sha256: str | None, require_marker: bool,
) -> SpeechUnit:
    manifest = Path(manifest_path).absolute()
    root = manifest.parent.resolve()
    try:
        if manifest.resolve(strict=True).parent != root or not manifest.is_file():
            raise ArtifactError("Unit manifest must be a file in its own directory")
        raw_manifest = manifest.read_bytes()
    except OSError as exc:
        raise ArtifactError(f"cannot read Unit manifest {manifest}: {exc}") from exc
    digest = hashlib.sha256(raw_manifest).hexdigest()
    if expected_sha256 is not None:
        if digest != _valid_hash(expected_sha256, "expected_sha256"):
            raise ArtifactError("Unit manifest SHA-256 does not match its pinned reference")
    if require_marker:
        marker = _confined_asset(root, ".complete", "completion marker")
        try:
            marker_bytes = marker.read_bytes()
        except OSError as exc:
            raise ArtifactError(f"cannot read Unit completion marker: {exc}") from exc
        if marker_bytes not in (
            digest.encode("ascii"), (digest + "\n").encode("ascii"),
            (digest + "\r\n").encode("ascii"),
        ):
            raise ArtifactError("Unit completion marker does not match manifest bytes")
    payload = _parse_json(raw_manifest, manifest)
    if not isinstance(payload, dict):
        raise ArtifactError("Unit manifest must be a JSON object")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ArtifactError("unsupported Unit schema_version")
    if payload.get("kind") != "speech_unit":
        raise ArtifactError("manifest kind must be speech_unit")
    content_id = _valid_hash(payload.get("content_id"), "content_id")
    identity = {key: value for key, value in payload.items() if key != "content_id"}
    if hashlib.sha256(_canonical(identity)).hexdigest() != content_id:
        raise ArtifactError("Unit content_id does not match its manifest content")
    audio = payload.get("audio")
    alignment = payload.get("alignment")
    provenance = payload.get("provenance")
    if not isinstance(audio, dict) or not isinstance(alignment, dict):
        raise ArtifactError("Unit audio and alignment must be objects")
    if not isinstance(provenance, dict):
        raise ArtifactError("Unit provenance must be an object")
    audio_path = _confined_asset(root, audio.get("path"), "audio.path")
    alignment_path = _confined_asset(root, alignment.get("path"), "alignment.path")
    if (
        audio_path.resolve() == alignment_path.resolve()
        or audio_path.resolve() == manifest.resolve()
        or alignment_path.resolve() == manifest.resolve()
        or audio.get("path") == ".complete" or alignment.get("path") == ".complete"
    ):
        raise ArtifactError("Unit assets must be distinct from manifest and marker")
    audio_hash = _valid_hash(audio.get("sha256"), "audio.sha256")
    alignment_hash = _valid_hash(alignment.get("sha256"), "alignment.sha256")
    sample_rate = _positive_integer(audio.get("sample_rate"), "audio.sample_rate")
    frames = _positive_integer(audio.get("frames"), "audio.frames")
    if sha256_file(audio_path) != audio_hash or sha256_file(alignment_path) != alignment_hash:
        raise ArtifactError("Unit asset SHA-256 does not match its manifest")
    samples, decoded_rate, decoded_frames, tokens, captions = _validated_data(
        audio_path, alignment_path, payload.get("text"), payload.get("language"),
    )
    if (sample_rate, frames) != (decoded_rate, decoded_frames):
        raise ArtifactError("Unit WAV metadata does not match its manifest")
    if (
        sha256_file(audio_path) != audio_hash or sha256_file(alignment_path) != alignment_hash
        or sha256_file(manifest) != digest
    ):
        raise ArtifactError("Unit changed while it was being verified")
    return SpeechUnit(
        manifest_path=manifest, manifest_sha256=digest, text=payload["text"],
        language=payload["language"], samples=samples, sample_rate=sample_rate,
        frames=frames, tokens=tokens, local_captions=captions, provenance=provenance,
        _audio_path=audio_path, _alignment_path=alignment_path, content_id=content_id,
    )


def load_unit(
    manifest_path: str | Path, *, expected_sha256: str | None = None,
) -> SpeechUnit:
    """Verify completion, identity, transcript, precise timing and asset bytes."""

    return _load_unit(manifest_path, expected_sha256=expected_sha256, require_marker=True)


def create_unit(
    output_dir: str | Path, *, audio_path: str | Path, alignment_path: str | Path,
    text: str, language: str, provenance: dict[str, Any],
) -> SpeechUnit:
    """Create a completed Unit in a new directory from immutable source inputs.

    Failure may leave diagnostic files in the new directory, but never a valid
    completion marker. Such a directory cannot be reused as a successful Unit.
    """

    destination = Path(output_dir).absolute()
    source_audio = Path(audio_path)
    source_alignment = Path(alignment_path)
    if not isinstance(provenance, dict):
        raise ArtifactError("Unit provenance must be an object")
    # Round-trip JSON also detaches nested caller-owned dictionaries/lists.
    provenance_copy = _parse_json(_canonical(provenance), Path("provenance"))
    audio_hash = sha256_file(source_audio)
    alignment_hash = sha256_file(source_alignment)
    _validated_data(source_audio, source_alignment, text, language)
    if sha256_file(source_audio) != audio_hash or sha256_file(source_alignment) != alignment_hash:
        raise ArtifactError("source inputs changed while being validated")
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise ArtifactError(f"Unit output directory must be new: {destination}: {exc}") from exc
    marker = destination / ".complete"
    try:
        copied_audio = destination / "audio.wav"
        copied_alignment = destination / "alignment.json"
        shutil.copyfile(source_audio, copied_audio)
        shutil.copyfile(source_alignment, copied_alignment)
        if (
            sha256_file(source_audio) != audio_hash
            or sha256_file(source_alignment) != alignment_hash
            or sha256_file(copied_audio) != audio_hash
            or sha256_file(copied_alignment) != alignment_hash
        ):
            raise ArtifactError("source inputs changed during Unit copying")
        _, sample_rate, frames, _, _ = _validated_data(
            copied_audio, copied_alignment, text, language,
        )
        payload: dict[str, Any] = {
            "schema_version": 1, "kind": "speech_unit", "text": text,
            "language": language,
            "audio": {"path": "audio.wav", "sha256": audio_hash,
                      "sample_rate": sample_rate, "frames": frames},
            "alignment": {"path": "alignment.json", "sha256": alignment_hash},
            "provenance": provenance_copy,
        }
        payload["content_id"] = hashlib.sha256(_canonical(payload)).hexdigest()
        manifest = destination / "unit.json"
        write_json(manifest, payload)
        verified = _load_unit(manifest, expected_sha256=None, require_marker=False)
        _atomic_write(marker, (verified.manifest_sha256 + "\n").encode("ascii"))
        return load_unit(manifest, expected_sha256=verified.manifest_sha256)
    except (ValueError, OSError) as exc:
        if marker.exists():
            marker.unlink()
        if isinstance(exc, ArtifactError):
            raise
        raise ArtifactError(f"could not create speech Unit: {exc}") from exc


def snapshot_unit(unit: SpeechUnit, output_dir: str | Path) -> SpeechUnit:
    """Copy a pinned, freshly verified Unit into independently owned assets."""

    verified = load_unit(unit.manifest_path, expected_sha256=unit.manifest_sha256)
    snapshot = create_unit(
        output_dir, audio_path=verified.audio_path, alignment_path=verified.alignment_path,
        text=verified.text, language=verified.language, provenance=verified.provenance,
    )
    try:
        if snapshot.content_id != verified.content_id:
            raise ArtifactError("source Unit changed before its snapshot was copied")
        load_unit(verified.manifest_path, expected_sha256=verified.manifest_sha256)
    except ArtifactError:
        # A source changed between the verification and copying boundaries.
        # Keep diagnostic data, but revoke completion of this failed snapshot.
        (snapshot.manifest_path.parent / ".complete").unlink()
        raise
    return snapshot
