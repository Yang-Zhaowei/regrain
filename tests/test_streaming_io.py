"""Regression checks for bounded PCM I/O and malformed production inputs."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import wave
from pathlib import Path

import pytest

import regrain.artifacts as artifacts
import regrain.audio_assembly as audio_assembly
import regrain.postprocess as postprocess
from regrain.artifacts import ArtifactError, create_unit, load_unit, sha256_file, write_json
from regrain.postprocess import AudioPostprocessError, read_wav, write_wav
from regrain.production import assemble_speech, relative_path
from regrain.wav_io import BLOCK_FRAMES, WavInfo, copy_wav_window, inspect_wav, inspect_wav_windows
from regrain.errors import RenderError


def _write_wave(path: Path, *, channels: int = 1, width: int = 2,
                rate: int = 100, frames: int = 20) -> bytes:
    """Write a small PCM wave with the requested header and return its raw bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(b"\x01" * frames * channels * width)
    return path.read_bytes()


def test_inspect_wav_reads_pcm_in_bounded_blocks(tmp_path, monkeypatch):
    path = tmp_path / "long.wav"
    samples = [((index * 37) % 60000) - 30000 for index in range(BLOCK_FRAMES * 2 + 17)]
    write_wav(path, samples, 24000)

    requested: list[int] = []
    original = wave.Wave_read.readframes

    def tracked(handle, count):
        requested.append(count)
        return original(handle, count)

    monkeypatch.setattr(wave.Wave_read, "readframes", tracked)
    info = inspect_wav(path)

    pcm = struct.pack("<" + "h" * len(samples), *samples)
    assert info == WavInfo(24000, len(samples), hashlib.sha256(pcm).hexdigest())
    assert requested and max(requested) <= BLOCK_FRAMES
    assert sum(requested) == len(samples)


@pytest.mark.parametrize("kind", ["stereo", "8bit", "empty", "truncated", "corrupt"])
def test_inspect_wav_rejects_unsupported_or_damaged_inputs(tmp_path, kind):
    path = tmp_path / f"{kind}.wav"
    if kind == "stereo":
        _write_wave(path, channels=2)
    elif kind == "8bit":
        _write_wave(path, width=1)
    elif kind == "empty":
        _write_wave(path, frames=0)
    elif kind == "truncated":
        raw = _write_wave(path, frames=20)
        path.write_bytes(raw[:-7])
    else:
        path.write_bytes(b"this is not a WAV")

    with pytest.raises(AudioPostprocessError):
        inspect_wav(path)


def test_copy_wav_window_preserves_exact_half_open_pcm_and_source(tmp_path):
    source = tmp_path / "source.wav"
    destination = tmp_path / "window.wav"
    samples = [-32768, -1, 0, 1, 32767, 1234, -2345]
    write_wav(source, samples, 24000)
    original = source.read_bytes()

    copied = copy_wav_window(
        source, destination, start_frame=1, end_frame=6, sample_rate=24000,
    )

    selected = samples[1:6]
    raw_pcm = struct.pack("<" + "h" * len(selected), *selected)
    assert copied == WavInfo(24000, 5, hashlib.sha256(raw_pcm).hexdigest())
    assert read_wav(destination) == (selected, 24000, 5)
    assert source.read_bytes() == original


def test_copy_wav_window_honors_start_and_end_boundaries(tmp_path):
    source = tmp_path / "source.wav"
    write_wav(source, [10, 20, 30, 40], 100)

    first = tmp_path / "first.wav"
    last = tmp_path / "last.wav"
    copy_wav_window(source, first, start_frame=0, end_frame=1, sample_rate=100)
    copy_wav_window(source, last, start_frame=3, end_frame=4, sample_rate=100)

    assert read_wav(first) == ([10], 100, 1)
    assert read_wav(last) == ([40], 100, 1)
    with pytest.raises(AudioPostprocessError):
        copy_wav_window(source, tmp_path / "empty.wav", start_frame=2, end_frame=2, sample_rate=100)


def test_copy_wav_window_rejects_destination_aliasing_source(tmp_path):
    source = tmp_path / "source.wav"
    write_wav(source, [10, 20, 30, 40], 100)
    original = source.read_bytes()

    with pytest.raises(AudioPostprocessError, match="source|destination|same"):
        copy_wav_window(source, source, start_frame=1, end_frame=3, sample_rate=100)

    assert source.read_bytes() == original


def test_copy_wav_window_rejects_hardlink_alias(tmp_path):
    source, destination = tmp_path / "source.wav", tmp_path / "alias.wav"
    write_wav(source, [10, 20, 30, 40], 100)
    original = source.read_bytes()
    os.link(source, destination)
    with pytest.raises(AudioPostprocessError, match="destination"):
        copy_wav_window(source, destination, start_frame=1, end_frame=3, sample_rate=100)
    assert source.read_bytes() == destination.read_bytes() == original


def test_window_pcm_hashes_cover_gaps_and_chunk_boundaries(tmp_path):
    source = tmp_path / "source.wav"
    samples = [((index * 37) % 60000) - 30000 for index in range(BLOCK_FRAMES * 2 + 17)]
    write_wav(source, samples, 24000)
    windows = ((1, 8), (BLOCK_FRAMES - 2, BLOCK_FRAMES + 3), (BLOCK_FRAMES + 5, len(samples)))
    info, digests = inspect_wav_windows(source, windows)
    assert info == inspect_wav(source)
    for (start, end), digest in zip(windows, digests):
        selected = samples[start:end]
        assert digest == hashlib.sha256(struct.pack("<" + "h" * len(selected), *selected)).hexdigest()


def test_external_snapshot_rejects_source_replaced_and_restored_during_copy(
    tmp_path, monkeypatch,
):
    import regrain.external_audio as external_audio

    audio = tmp_path / "recording.wav"
    request = tmp_path / "alignment.json"
    write_wav(audio, [1000] * 100, 100)
    original_bytes = audio.read_bytes()
    write_json(request, {
        "schema_version": 1,
        "language": "Chinese",
        "segments": [{"id": "recorded", "audio": "recording.wav", "text": "你好。"}],
    })
    original_copy = external_audio.copy_wav_window

    def replace_during_copy(source, destination, **kwargs):
        assert Path(source) == audio
        write_wav(audio, [-1000] * 100, 100)
        copied = original_copy(source, destination, **kwargs)
        audio.write_bytes(original_bytes)
        return copied

    monkeypatch.setattr(external_audio, "copy_wav_window", replace_during_copy)
    model_calls = []

    def fake_aligner(task):
        model_calls.append(task)
        return {"segments": [{"segment_id": "recorded", "status": "ok"}]}

    with pytest.raises(RenderError, match="changed|source|snapshot"):
        external_audio.align_speech(
            request, tmp_path / "run", alignment_python="unused",
            alignment_model="unused", _align_task=fake_aligner,
        )

    assert model_calls == []
    assert audio.read_bytes() == original_bytes
    assert not (tmp_path / "run" / "final" / ".complete").exists()


def test_unit_rejects_overlapping_compiled_captions_without_completion(tmp_path):
    audio = tmp_path / "audio.wav"
    alignment = tmp_path / "alignment.json"
    text = "你" * 60
    write_wav(audio, [0] * 500, 10)
    write_json(alignment, [{"text": text, "start": 0.0, "end": 50.0}])
    destination = tmp_path / "unit"

    with pytest.raises(ArtifactError, match="overlap"):
        create_unit(
            destination, audio_path=audio, alignment_path=alignment,
            text=text, language="Chinese", provenance={"test": "long-single-token"},
        )

    # Invalid source semantics fail before creating an output directory.
    assert not destination.exists()
    assert not (destination / ".complete").exists()


def test_relative_path_translates_symlink_loop_resolution_error(tmp_path, monkeypatch):
    original_resolve = Path.resolve

    def loop_error(path, *args, **kwargs):
        if path.name == "loop.wav":
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", loop_error)

    with pytest.raises(ArtifactError, match="resolve|symlink|loop"):
        relative_path("loop.wav", tmp_path)


def test_cli_reports_symlink_loop_as_validation_error(tmp_path, monkeypatch, capsys):
    from regrain.cli import main

    request = tmp_path / "alignment.json"
    write_json(request, {
        "schema_version": 1,
        "language": "Chinese",
        "segments": [{"id": "loop", "audio": "loop.wav", "text": "你好。"}],
    })
    original_resolve = Path.resolve

    def loop_error(path, *args, **kwargs):
        if path.name == "loop.wav":
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", loop_error)
    code = main(["speech", "align", str(request), "--output", str(tmp_path / "run")])

    assert code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "validation_failed"
    assert "loop" in payload["error"]
    assert not (tmp_path / "run").exists()


def test_real_symlink_loop_is_a_validation_error_when_supported(tmp_path, capsys):
    from regrain.cli import main

    loop = tmp_path / "loop.wav"
    try:
        loop.symlink_to(loop.name)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"creating symlinks is unavailable on this host: {exc}")
    request = tmp_path / "alignment.json"
    write_json(request, {
        "schema_version": 1,
        "language": "Chinese",
        "segments": [{"id": "loop", "audio": "loop.wav", "text": "你好。"}],
    })

    code = main(["speech", "align", str(request), "--output", str(tmp_path / "run")])

    assert code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "validation_failed"
    assert not (tmp_path / "run").exists()


def test_assembly_streams_existing_unit_assets_without_full_decode(tmp_path, monkeypatch):
    audio = tmp_path / "source.wav"
    alignment = tmp_path / "source.json"
    write_wav(audio, list(range(200)), 100)
    write_json(alignment, [
        {"text": "你好", "start": 0.123456789, "end": 0.956789123},
        {"text": "世界", "start": 1.256789123, "end": 1.987654321},
    ])
    unit = create_unit(
        tmp_path / "unit", audio_path=audio, alignment_path=alignment,
        text="你好，世界。", language="Chinese", provenance={"stage": "test"},
    )
    # Public API remains eager and returns the same sample-list contract.
    assert load_unit(unit.manifest_path).samples == list(range(200))

    plan = tmp_path / "plan.json"
    write_json(plan, {
        "schema_version": 1,
        "units": [{
            "id": "one", "artifact": "unit/unit.json",
            "sha256": unit.manifest_sha256, "pause_after_ms": 0,
        }],
    })
    source_hash = sha256_file(unit.audio_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("production assembly performed an eager WAV decode")

    monkeypatch.setattr(artifacts, "read_wav", forbidden)
    monkeypatch.setattr(postprocess, "read_wav", forbidden)
    monkeypatch.setattr(audio_assembly, "read_wav", forbidden, raising=False)

    result = assemble_speech(plan, tmp_path / "assembled")

    assert result.status == "complete"
    assert sha256_file(unit.audio_path) == source_hash
    assert result.wav_path.is_file() and result.srt_path.is_file() and result.timeline_path.is_file()


def test_complete_marker_is_not_visible_until_atomic_publication(tmp_path, monkeypatch):
    final = tmp_path / "final"
    final.mkdir()
    original_replace = artifacts.os.replace
    observations = []

    def replace(source, destination):
        if Path(destination) == final / ".complete":
            observations.append((Path(destination).exists(), Path(source).read_bytes()))
        return original_replace(source, destination)

    monkeypatch.setattr(artifacts.os, "replace", replace)
    audio_assembly._publish_complete_marker(final)
    assert observations == [(False, b"complete")]
    assert (final / ".complete").read_bytes() == b"complete"


def test_public_unit_manifest_symlink_loop_is_an_artifact_error(tmp_path, monkeypatch):
    original_resolve = Path.resolve

    def loop(path, *args, **kwargs):
        if path.name == "loop.json":
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", loop)
    with pytest.raises(ArtifactError, match="symlink loop"):
        load_unit(tmp_path / "loop.json")


def test_transient_wrong_pcm_read_cannot_pass_unchanged_file_hashes(tmp_path, monkeypatch):
    audio, alignment = tmp_path / "source.wav", tmp_path / "alignment.json"
    write_wav(audio, [1234] * 200, 100)
    write_json(alignment, [{"text": "你好", "start": 0.2, "end": 1.5}])
    unit = create_unit(tmp_path / "unit", audio_path=audio, alignment_path=alignment,
                       text="你好。", language="Chinese", provenance={})
    plan = tmp_path / "plan.json"
    write_json(plan, {"schema_version": 1, "units": [{"id": "a", "artifact": "unit/unit.json",
                                                       "sha256": unit.manifest_sha256}]})
    original_writer = audio_assembly._write_assembled_wav
    original_read = wave.Wave_read.readframes

    def corrupted_writer(path, segments, rate):
        def read(handle, count):
            raw = original_read(handle, count)
            return bytes([raw[0] ^ 1]) + raw[1:] if raw else raw
        # Alter only the stream consumed by assembly; all disk assets/hashes
        # remain valid. Expected PCM digest must detect the read corruption.
        with monkeypatch.context() as patch:
            patch.setattr(wave.Wave_read, "readframes", read)
            return original_writer(path, segments, rate)

    monkeypatch.setattr(audio_assembly, "_write_assembled_wav", corrupted_writer)
    with pytest.raises(RenderError, match="PCM changed"):
        assemble_speech(plan, tmp_path / "run")
    assert not (tmp_path / "run" / "final" / ".complete").exists()
    assert load_unit(unit.manifest_path).samples == [1234] * 200
