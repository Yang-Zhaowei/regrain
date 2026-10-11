"""CPU-only integrity and immutability tests for portable speech Units."""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import pytest

import regrain.artifacts as artifacts
from regrain.artifacts import (
    ArtifactError, create_unit, load_unit, read_json, sha256_file,
    snapshot_unit, write_json,
)
from regrain.postprocess import read_wav, write_wav


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, Path]:
    audio = tmp_path / "input.wav"
    alignment = tmp_path / "input.json"
    write_wav(audio, list(range(200)), 100)
    write_json(alignment, [
        {"text": "你好", "start": 0.123456789, "end": 0.956789123},
        {"text": "世界", "start": 1.256789123, "end": 1.987654321},
    ])
    return audio, alignment


def _unit(tmp_path: Path, source: tuple[Path, Path]):
    return create_unit(
        tmp_path / "unit", audio_path=source[0], alignment_path=source[1],
        text="你好，世界。", language="Chinese", provenance={"stage": "test"},
    )


def _reseal(manifest: Path, payload: dict) -> None:
    """Recompute envelope integrity to test independent semantic validation."""

    unsigned = {key: value for key, value in payload.items() if key != "content_id"}
    canonical = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    payload["content_id"] = hashlib.sha256(canonical).hexdigest()
    write_json(manifest, payload)
    (manifest.parent / ".complete").write_text(sha256_file(manifest) + "\n", encoding="ascii")


def test_unit_owns_exact_input_bytes_and_precise_alignment(tmp_path, source):
    before = [path.read_bytes() for path in source]
    unit = _unit(tmp_path, source)
    loaded = load_unit(unit.manifest_path, expected_sha256=unit.manifest_sha256)

    assert loaded.audio_path.read_bytes() == before[0]
    assert loaded.alignment_path.read_bytes() == before[1]
    assert [path.read_bytes() for path in source] == before
    assert loaded.samples == list(range(200))
    assert loaded.sample_rate == 100 and loaded.frames == 200
    assert loaded.duration == 2
    assert loaded.tokens[0].start == 0.123456789
    assert loaded.local_captions[0].start == 0.123456789
    assert "".join(caption.text for caption in loaded.local_captions) == "你好，世界。"
    payload = read_json(unit.manifest_path)
    assert payload["audio"]["path"] == "audio.wav"
    assert payload["alignment"]["path"] == "alignment.json"
    assert str(tmp_path) not in unit.manifest_path.read_text(encoding="utf-8")


def test_snapshot_is_independent_and_reverifies_original(tmp_path, source):
    original = _unit(tmp_path, source)
    snapshot = snapshot_unit(original, tmp_path / "snapshot")
    assert snapshot.content_id == original.content_id
    assert snapshot.manifest_sha256 == original.manifest_sha256
    original.audio_path.write_bytes(b"corrupted after snapshot")
    assert load_unit(snapshot.manifest_path).samples == list(range(200))
    with pytest.raises(ArtifactError, match="asset SHA-256"):
        snapshot_unit(original, tmp_path / "second-snapshot")


def test_snapshot_rejects_source_change_between_verify_and_copy(tmp_path, source, monkeypatch):
    original = _unit(tmp_path, source)
    original_create = artifacts.create_unit

    def mutate_before_copy(*args, **kwargs):
        samples, rate, _ = read_wav(original.audio_path)
        samples[0] += 1
        write_wav(original.audio_path, samples, rate)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(artifacts, "create_unit", mutate_before_copy)
    with pytest.raises(ArtifactError, match="changed before its snapshot"):
        snapshot_unit(original, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot" / ".complete").exists()


@pytest.mark.parametrize("target", ["audio.wav", "alignment.json"])
def test_tampered_asset_is_rejected(tmp_path, source, target):
    unit = _unit(tmp_path, source)
    with (unit.manifest_path.parent / target).open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(ArtifactError, match="asset SHA-256"):
        load_unit(unit.manifest_path)


def test_wrong_manifest_pin_is_rejected(tmp_path, source):
    unit = _unit(tmp_path, source)
    with pytest.raises(ArtifactError, match="pinned reference"):
        load_unit(unit.manifest_path, expected_sha256="0" * 64)
    with pytest.raises(ArtifactError, match="lowercase SHA-256"):
        load_unit(unit.manifest_path, expected_sha256=True)


@pytest.mark.parametrize("marker", [None, b"", b"not-a-hash", b"0" * 64, b" \n"])
def test_missing_or_malformed_completion_is_rejected(tmp_path, source, marker):
    unit = _unit(tmp_path, source)
    path = unit.manifest_path.parent / ".complete"
    if marker is None:
        path.unlink()
    else:
        path.write_bytes(marker)
    with pytest.raises(ArtifactError):
        load_unit(unit.manifest_path)


def test_exact_manifest_bytes_are_authenticated(tmp_path, source):
    unit = _unit(tmp_path, source)
    with unit.manifest_path.open("ab") as handle:
        handle.write(b"\n")
    with pytest.raises(ArtifactError, match="completion marker"):
        load_unit(unit.manifest_path)


def test_manifest_content_id_binds_transcript_and_provenance(tmp_path, source):
    unit = _unit(tmp_path, source)
    payload = read_json(unit.manifest_path)
    payload["provenance"]["stage"] = "tampered"
    write_json(unit.manifest_path, payload)
    (unit.manifest_path.parent / ".complete").write_text(
        sha256_file(unit.manifest_path) + "\n", encoding="ascii",
    )
    with pytest.raises(ArtifactError, match="content_id"):
        load_unit(unit.manifest_path)


def test_resealed_mismatching_transcript_is_rejected(tmp_path, source):
    unit = _unit(tmp_path, source)
    payload = read_json(unit.manifest_path)
    payload["text"] = "另一个讲稿。"
    _reseal(unit.manifest_path, payload)
    with pytest.raises(ArtifactError, match="original text"):
        load_unit(unit.manifest_path)


@pytest.mark.parametrize("field,value", [
    ("sample_rate", True), ("frames", False), ("sample_rate", 200),
    ("frames", 201), ("frames", 200.0),
])
def test_resealed_wrong_or_boolean_metadata_is_rejected(tmp_path, source, field, value):
    unit = _unit(tmp_path, source)
    payload = read_json(unit.manifest_path)
    payload["audio"][field] = value
    _reseal(unit.manifest_path, payload)
    with pytest.raises(ArtifactError, match="integer|metadata"):
        load_unit(unit.manifest_path)


def test_resealed_boolean_schema_version_is_rejected(tmp_path, source):
    unit = _unit(tmp_path, source)
    payload = read_json(unit.manifest_path)
    payload["schema_version"] = True
    _reseal(unit.manifest_path, payload)
    with pytest.raises(ArtifactError, match="schema_version"):
        load_unit(unit.manifest_path)


def test_resealed_stale_alignment_is_rejected(tmp_path, source):
    unit = _unit(tmp_path, source)
    alignment = read_json(unit.alignment_path)
    alignment[-1]["end"] = 2.1
    write_json(unit.alignment_path, alignment)
    payload = read_json(unit.manifest_path)
    payload["alignment"]["sha256"] = sha256_file(unit.alignment_path)
    _reseal(unit.manifest_path, payload)
    with pytest.raises(ArtifactError, match="past the WAV duration"):
        load_unit(unit.manifest_path)


@pytest.mark.parametrize("asset_path", [
    "../input.wav", "/input.wav", "C:/input.wav", "C:input.wav",
    "file:///input.wav", "..\\input.wav", "folder/../../input.wav",
    "folder\\audio.wav", "./audio.wav", "folder//audio.wav",
])
def test_confined_paths_reject_portable_and_windows_traversal(tmp_path, source, asset_path):
    unit = _unit(tmp_path, source)
    payload = read_json(unit.manifest_path)
    payload["audio"]["path"] = asset_path
    _reseal(unit.manifest_path, payload)
    with pytest.raises(ArtifactError, match="confined relative POSIX path"):
        load_unit(unit.manifest_path)


def test_symlink_asset_cannot_escape_unit_directory(tmp_path, source):
    unit = _unit(tmp_path, source)
    linked = unit.manifest_path.parent / "outside.wav"
    try:
        linked.symlink_to(source[0])
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"creating symlinks is unavailable on this host: {exc}")
    payload = read_json(unit.manifest_path)
    payload["audio"]["path"] = "outside.wav"
    _reseal(unit.manifest_path, payload)
    with pytest.raises(ArtifactError, match="escapes"):
        load_unit(unit.manifest_path)


def test_input_mutation_during_copy_never_creates_completion(tmp_path, source, monkeypatch):
    original_copy = artifacts.shutil.copyfile

    def copying_then_mutating(src, dst):
        result = original_copy(src, dst)
        if Path(src) == source[0]:
            samples, rate, _ = read_wav(source[0])
            samples[0] += 1
            write_wav(source[0], samples, rate)
        return result

    monkeypatch.setattr(artifacts.shutil, "copyfile", copying_then_mutating)
    destination = tmp_path / "unit"
    with pytest.raises(ArtifactError, match="changed during Unit copying"):
        _unit(tmp_path, source)
    assert destination.is_dir()
    assert not (destination / ".complete").exists()
    monkeypatch.setattr(artifacts.shutil, "copyfile", original_copy)
    with pytest.raises(ArtifactError, match="must be new"):
        _unit(tmp_path, source)
    with pytest.raises(ArtifactError):
        load_unit(destination / "unit.json")


def test_copy_failure_leaves_no_success_marker(tmp_path, source, monkeypatch):
    original_copy = artifacts.shutil.copyfile

    def failing_copy(src, dst):
        if Path(src) == source[1]:
            raise OSError("controlled disk failure")
        return original_copy(src, dst)

    monkeypatch.setattr(artifacts.shutil, "copyfile", failing_copy)
    with pytest.raises(ArtifactError, match="controlled disk failure"):
        _unit(tmp_path, source)
    assert not (tmp_path / "unit" / ".complete").exists()


def test_existing_output_and_sources_are_never_overwritten(tmp_path, source):
    before = [path.read_bytes() for path in source]
    (tmp_path / "unit").mkdir()
    sentinel = tmp_path / "unit" / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(ArtifactError, match="must be new"):
        _unit(tmp_path, source)
    assert sentinel.read_text() == "keep"
    assert [path.read_bytes() for path in source] == before
    with pytest.raises(ArtifactError, match="must be new"):
        create_unit(
            tmp_path, audio_path=source[0], alignment_path=source[1],
            text="你好，世界。", language="Chinese", provenance={},
        )
    assert [path.read_bytes() for path in source] == before


@pytest.mark.parametrize("document", [
    '{"key":1,"key":2}', '{"nested":{"key":1,"key":2}}',
    '[NaN]', '[Infinity]', '[-Infinity]', '[1e999]',
])
def test_json_reader_rejects_duplicates_and_nonfinite_numbers(tmp_path, document):
    path = tmp_path / "invalid.json"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ArtifactError):
        read_json(path)


def test_failed_atomic_json_write_preserves_original(tmp_path, monkeypatch):
    destination = tmp_path / "data.json"
    write_json(destination, {"old": True})

    def fail_replace(src, dst):
        raise OSError("controlled replacement failure")

    monkeypatch.setattr(artifacts.os, "replace", fail_replace)
    with pytest.raises(ArtifactError, match="replacement failure"):
        write_json(destination, {"new": True})
    assert read_json(destination) == {"old": True}
    assert list(tmp_path.iterdir()) == [destination]


def test_provenance_is_detached_from_caller(tmp_path, source):
    provenance = {"inputs": [{"hash": "abc"}]}
    unit = create_unit(
        tmp_path / "unit", audio_path=source[0], alignment_path=source[1],
        text="你好，世界。", language="Chinese", provenance=provenance,
    )
    provenance["inputs"][0]["hash"] = "changed"
    assert unit.provenance == {"inputs": [{"hash": "abc"}]}


@pytest.mark.parametrize("channels,bits,frames", [(2, 16, 200), (1, 8, 200), (1, 16, 0)])
def test_invalid_wav_format_or_empty_audio_is_rejected(tmp_path, source, channels, bits, frames):
    with wave.open(str(source[0]), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(bits // 8)
        handle.setframerate(100)
        handle.writeframes(b"\0" * frames * channels * (bits // 8))
    with pytest.raises(ArtifactError):
        _unit(tmp_path, source)
    assert not (tmp_path / "unit" / ".complete").exists()


def test_truncated_wav_is_rejected_without_success(tmp_path, source):
    source[0].write_bytes(source[0].read_bytes()[:-1])
    with pytest.raises(ArtifactError, match="invalid"):
        _unit(tmp_path, source)
    assert not (tmp_path / "unit" / ".complete").exists()


@pytest.mark.parametrize("alignment", [
    {}, [], [{"text": "你好世界", "start": True, "end": 1}],
    [{"text": "你好", "start": 0, "end": 1}, {"text": "世界", "start": 0.5, "end": 1.5}],
    [{"text": "你好", "start": 0, "end": 1}],
])
def test_invalid_alignment_or_missing_text_is_rejected(tmp_path, source, alignment):
    write_json(source[1], alignment)
    with pytest.raises(ArtifactError):
        _unit(tmp_path, source)
    assert not (tmp_path / "unit" / ".complete").exists()


@pytest.mark.parametrize("end", [0.2, 0.2001])
def test_zero_or_submillisecond_captions_cannot_complete_unit(tmp_path, source, end):
    write_json(source[1], [{"text": "你好世界", "start": 0.2, "end": end}])
    with pytest.raises(ArtifactError, match="no usable duration"):
        _unit(tmp_path, source)
    assert not (tmp_path / "unit" / ".complete").exists()


def test_individual_zero_duration_token_with_usable_caption_remains_valid(tmp_path, source):
    write_json(source[1], [
        {"text": "你", "start": 0.2, "end": 0.2},
        {"text": "好世界", "start": 0.2, "end": 1.5},
    ])
    assert _unit(tmp_path, source).local_captions[0].end == 1.5


@pytest.mark.parametrize("text,language", [
    ("", "Chinese"), ("。！？", "Chinese"), ("你好，世界。", ""),
    (True, "Chinese"), ("你好，世界。", True),
])
def test_invalid_transcript_language_never_produce_completion(tmp_path, source, text, language):
    with pytest.raises(ArtifactError):
        create_unit(
            tmp_path / "unit", audio_path=source[0], alignment_path=source[1],
            text=text, language=language, provenance={},
        )
    assert not (tmp_path / "unit" / ".complete").exists()
