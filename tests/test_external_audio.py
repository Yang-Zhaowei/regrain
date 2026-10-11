"""CPU production tests for supplied recordings; forced alignment is faked.

The portable validators, Unit publication, captions, and assembly run for real.
These tests are deliberately not model accuracy or GPU acceptance evidence.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from regrain import external_audio, render
from regrain.artifacts import ArtifactError, load_unit, read_json, sha256_file, write_json
from regrain.captions import has_alignable_content
from regrain.external_audio import align_speech, load_audio_request
from regrain.postprocess import read_wav, write_wav
from regrain.render import RenderError


class FakeExternalAligner:
    """One model-stage call processes all snapshots using their actual durations."""

    def __init__(self, *, bad: dict | None = None, fail_ids=(), runtime_after=None):
        self.calls = []
        self.bad = bad or {}
        self.fail_ids = set(fail_ids)
        self.runtime_after = runtime_after

    def __call__(self, task: dict) -> dict:
        self.calls.append(task)
        directory = Path(task["run_dir"]) / "segments"
        entries = []
        for index, segment in enumerate(task["segments"]):
            if self.runtime_after is not None and index >= self.runtime_after:
                return {"runtime_error": "controlled model-stage crash", "segments": entries}
            if segment["id"] in self.fail_ids:
                entries.append({"segment_id": segment["id"], "status": "alignment_failed",
                                "reason": "controlled transcript mismatch"})
                continue
            audio = directory / (segment["safe_name"] + ".wav")
            _, rate, frames = read_wav(audio)
            records = self.bad.get(segment["id"], [{
                "text": "".join(char for char in segment["text"] if has_alignable_content(char)),
                "start": 0.125, "end": frames / rate - 0.125,
            }])
            # Deliberately permit invalid model data; production must reject it.
            (directory / (segment["safe_name"] + ".alignment.raw.json")).write_text(
                json.dumps(records, ensure_ascii=False), encoding="utf-8",
            )
            entries.append({"segment_id": segment["id"], "status": "ok"})
        return {"runtime_error": None, "segments": entries}


def write_audio_request(tmp_path: Path, *, segments=None, processing=None, samples=None,
                        rate=100) -> tuple[Path, Path]:
    audio = tmp_path / "真人录音.wav"
    samples = samples if samples is not None else ([0] * 40 + [1000] * 120 + [0] * 60 + [2000] * 100 + [0] * 80)
    write_wav(audio, samples, rate)
    payload = {"schema_version": 1, "language": "Chinese", "segments": segments or [
        {"id": "真人旁白", "audio": audio.name, "text": "准确讲稿。"},
    ]}
    if processing is not None:
        payload["processing"] = processing
    request = tmp_path / "audio.json"
    write_json(request, payload)
    return request, audio


def run_alignment(request: Path, output: Path, task: FakeExternalAligner):
    return align_speech(request, output, alignment_python="fake-align-python",
                        alignment_model="fake-align-model", device="cpu", _align_task=task)


def forbid_call(*args, **kwargs):
    pytest.fail("external alignment must never invoke TTS")


def test_full_recording_preserves_all_pcm_and_source_bytes(tmp_path, monkeypatch):
    request, original = write_audio_request(tmp_path)
    original_bytes = original.read_bytes()
    original_samples, rate, frames = read_wav(original)
    monkeypatch.setattr(render, "_make_tts_task", forbid_call)
    fake = FakeExternalAligner()
    result = run_alignment(request, tmp_path / "run", fake)

    assert result.status == "complete"
    assert len(fake.calls) == 1
    assert fake.calls[0]["device"] == "cpu" and fake.calls[0]["language"] == "Chinese"
    assert read_wav(result.wav_path) == (original_samples, rate, frames)
    assert original.read_bytes() == original_bytes
    report = read_json(result.report_path)
    assert report["status"] == "complete"
    assert report["completed_stages"] == ["snapshot", "alignment", "units", "assemble"]
    unit = load_unit(result.run_dir / report["segments"][0]["unit_manifest"])
    assert unit.samples == original_samples
    assert unit.provenance["source_audio_sha256"] == sha256_file(original)
    assert unit.provenance["source_start_frame"] == 0
    assert unit.provenance["source_end_frame"] == frames
    assert unit.provenance["processing"] == "preserve"
    assert (result.wav_path.parent / ".complete").read_text() == "complete"
    assert "准确讲稿。" in result.srt_path.read_text(encoding="utf-8")
    timeline = read_json(result.timeline_path)
    assert timeline["total_frames"] == frames
    assert timeline["segments"][0]["start_frame"] == 0


def test_explicit_contiguous_chunks_preserve_full_recording_and_global_offsets(tmp_path):
    samples = list(range(300))
    segments = [
        {"id": f"句{i}", "audio": "真人录音.wav", "text": f"第{i}句。",
         "start_frame": (i - 1) * 100, "end_frame": i * 100}
        for i in range(1, 4)
    ]
    request, original = write_audio_request(tmp_path, segments=segments, samples=samples)
    before = original.read_bytes()
    fake = FakeExternalAligner()
    result = run_alignment(request, tmp_path / "run", fake)
    assert len(fake.calls) == 1 and len(fake.calls[0]["segments"]) == 3
    assert read_wav(result.wav_path) == (samples, 100, 300)
    timeline = read_json(result.timeline_path)
    assert [item["start_frame"] for item in timeline["segments"]] == [0, 100, 200]
    assert [item["audio_frames"] for item in timeline["segments"]] == [100] * 3
    srt = result.srt_path.read_text(encoding="utf-8")
    assert "00:00:00,125 --> 00:00:00,875" in srt
    assert "00:00:01,125 --> 00:00:01,875" in srt
    assert "00:00:02,125 --> 00:00:02,875" in srt
    report = read_json(result.report_path)
    for index, entry in enumerate(report["segments"]):
        provenance = load_unit(result.run_dir / entry["unit_manifest"]).provenance
        assert provenance["source_start_frame"] == index * 100
        assert provenance["source_end_frame"] == (index + 1) * 100
    assert original.read_bytes() == before


def test_explicit_gaps_and_pause_are_exactly_authored(tmp_path):
    samples = list(range(400))
    request, _ = write_audio_request(tmp_path, samples=samples, segments=[
        {"id": "a", "audio": "真人录音.wav", "text": "甲。", "start_frame": 0,
         "end_frame": 100, "pause_after_ms": 25},
        {"id": "b", "audio": "真人录音.wav", "text": "乙。", "start_frame": 200,
         "end_frame": 300},
    ])
    result = run_alignment(request, tmp_path / "run", FakeExternalAligner())
    assert read_wav(result.wav_path) == (samples[:100] + [0] * 3 + samples[200:300], 100, 203)
    timeline = read_json(result.timeline_path)
    assert timeline["segments"][0]["pause_after_frames"] == 3
    assert timeline["segments"][1]["start_frame"] == 103
    assert "00:00:01,155" in result.srt_path.read_text(encoding="utf-8")


def test_same_recording_is_decoded_once_during_preflight(tmp_path, monkeypatch):
    request, _ = write_audio_request(tmp_path, segments=[
        {"id": "a", "audio": "真人录音.wav", "text": "甲。", "start_frame": 0, "end_frame": 100},
        {"id": "b", "audio": "真人录音.wav", "text": "乙。", "start_frame": 100, "end_frame": 200},
    ])
    original_read = external_audio.read_wav
    calls = []

    def recording_read(path):
        calls.append(Path(path))
        return original_read(path)

    monkeypatch.setattr(external_audio, "read_wav", recording_read)
    _, windows = load_audio_request(request)
    assert len(windows) == 2 and len(calls) == 1


@pytest.mark.parametrize("processing", [None, "preserve"])
def test_default_and_explicit_preserve_are_equivalent(tmp_path, processing):
    request, original = write_audio_request(tmp_path, processing=processing)
    result = run_alignment(request, tmp_path / "run", FakeExternalAligner())
    assert read_wav(result.wav_path) == read_wav(original)


def test_trim_is_explicit_and_does_not_change_original(tmp_path):
    request, original = write_audio_request(tmp_path, processing="trim")
    before = original.read_bytes()
    fake = FakeExternalAligner(bad={"真人旁白": [{"text": "准确讲稿", "start": 1.0, "end": 2.0}]})
    result = run_alignment(request, tmp_path / "run", fake)
    _, rate, frames = read_wav(result.wav_path)
    assert rate == 100 and frames == 130  # 15 frames of padding on either side.
    unit = load_unit(result.run_dir / "units" / "0001" / "unit.json")
    assert unit.tokens[0].start == pytest.approx(0.15)
    assert unit.tokens[0].end == pytest.approx(1.15)
    assert unit.provenance["processing"] == "trim"
    assert original.read_bytes() == before


@pytest.mark.parametrize("start,end", [(0, 301), (0, 0), (2, 1), (0, 302), (-1, 10), (True, 10), (0, 10.0)])
def test_invalid_or_too_long_windows_fail_before_model_or_output(tmp_path, start, end):
    request, _ = write_audio_request(tmp_path, samples=[0] * 301, rate=1, segments=[
        {"id": "a", "audio": "真人录音.wav", "text": "甲。", "start_frame": start, "end_frame": end},
    ])
    fake = FakeExternalAligner()
    with pytest.raises(RenderError) as caught:
        run_alignment(request, tmp_path / "run", fake)
    assert caught.value.run_dir is None
    assert fake.calls == [] and not (tmp_path / "run").exists()


def test_exact_300_second_window_is_accepted_by_preflight(tmp_path):
    request, _ = write_audio_request(tmp_path, samples=[0] * 300, rate=1)
    assert len(load_audio_request(request)[1][0].samples) == 300


@pytest.mark.parametrize("overrides", [
    {"start_frame": 0}, {"end_frame": 100}, {"pause_after_ms": -1},
    {"pause_after_ms": True}, {"text": "。？！"}, {"text": ""},
    {"audio": "file:///recording.wav"}, {"audio": "C:/recording.wav"}, {"unknown": 1},
])
def test_malformed_segments_are_rejected_before_stage(tmp_path, overrides):
    request, _ = write_audio_request(tmp_path, segments=[{
        "id": "a", "audio": "真人录音.wav", "text": "甲。", **overrides,
    }])
    fake = FakeExternalAligner()
    with pytest.raises(RenderError):
        run_alignment(request, tmp_path / "run", fake)
    assert fake.calls == [] and not (tmp_path / "run").exists()


@pytest.mark.parametrize("windows", [[(0, 150), (100, 200)], [(200, 300), (0, 100)]])
def test_overlap_or_out_of_order_windows_are_rejected(tmp_path, windows):
    request, _ = write_audio_request(tmp_path, segments=[
        {"id": str(i), "audio": "真人录音.wav", "text": "讲稿。", "start_frame": start, "end_frame": end}
        for i, (start, end) in enumerate(windows)
    ])
    with pytest.raises(ArtifactError, match="ordered and non-overlapping"):
        load_audio_request(request)


def test_stereo_recording_is_rejected_before_models(tmp_path):
    request, audio = write_audio_request(tmp_path)
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(100)
        handle.writeframes(b"\0" * 400)
    fake = FakeExternalAligner()
    with pytest.raises(RenderError, match="mono 16-bit PCM"):
        run_alignment(request, tmp_path / "run", fake)
    assert not fake.calls and not (tmp_path / "run").exists()


def test_recording_rate_mismatch_is_rejected(tmp_path):
    write_wav(tmp_path / "other.wav", [1] * 200, 200)
    request, _ = write_audio_request(tmp_path, segments=[
        {"id": "a", "audio": "真人录音.wav", "text": "甲。"},
        {"id": "b", "audio": "other.wav", "text": "乙。"},
    ])
    with pytest.raises(ArtifactError, match="sample rate mismatch"):
        load_audio_request(request)


def test_duplicate_segment_ids_are_rejected_before_models(tmp_path):
    request, _ = write_audio_request(tmp_path, segments=[
        {"id": "same", "audio": "真人录音.wav", "text": "甲。", "start_frame": 0, "end_frame": 100},
        {"id": "same", "audio": "真人录音.wav", "text": "乙。", "start_frame": 100, "end_frame": 200},
    ])
    fake = FakeExternalAligner()
    with pytest.raises(RenderError, match="duplicate segment id"):
        run_alignment(request, tmp_path / "run", fake)
    assert not fake.calls and not (tmp_path / "run").exists()


def test_source_mutation_while_decoding_preflight_is_detected(tmp_path, monkeypatch):
    request, original = write_audio_request(tmp_path)
    original_reader = external_audio.read_wav

    def reading_then_mutating(path):
        samples, rate, frames = original_reader(path)
        changed = samples.copy()
        changed[0] += 1
        write_wav(original, changed, rate)
        return samples, rate, frames

    monkeypatch.setattr(external_audio, "read_wav", reading_then_mutating)
    with pytest.raises(ArtifactError, match="changed during preflight"):
        load_audio_request(request)


@pytest.mark.parametrize("records", [
    [{"text": "错误讲稿", "start": 0.1, "end": 0.8}],
    [{"text": "甲", "start": 0.1, "end": 1.1}],
    [{"text": "甲", "start": -0.1, "end": 0.8}],
    [{"text": "甲", "start": float("nan"), "end": 0.8}],
    [{"text": "甲", "start": 0.1, "end": float("inf")}],
    [{"text": "甲", "start": 0.2, "end": 0.2}],
    [{"text": "甲", "start": 0.2, "end": 0.2001}],
])
def test_semantic_alignment_failure_isolates_and_preserves_good_units(tmp_path, records):
    request, original = write_audio_request(tmp_path, segments=[
        {"id": "bad", "audio": "真人录音.wav", "text": "甲。", "start_frame": 0, "end_frame": 100},
        {"id": "good", "audio": "真人录音.wav", "text": "乙。", "start_frame": 100, "end_frame": 200},
    ])
    before = original.read_bytes()
    result = run_alignment(request, tmp_path / "run", FakeExternalAligner(bad={"bad": records}))
    assert result.status == "incomplete" and result.wav_path is None
    assert [item.status for item in result.segments] == ["alignment_failed", "ok"]
    assert load_unit(result.run_dir / "units" / "0002" / "unit.json").text == "乙。"
    assert not (result.run_dir / "units" / "0001" / ".complete").exists()
    assert not (result.run_dir / "final" / ".complete").exists()
    assert read_json(result.report_path)["status"] == "incomplete"
    assert original.read_bytes() == before


def test_model_reported_validation_failure_is_incomplete(tmp_path):
    request, _ = write_audio_request(tmp_path)
    result = run_alignment(request, tmp_path / "run", FakeExternalAligner(fail_ids={"真人旁白"}))
    assert result.status == "incomplete"
    assert result.segments[0].status == "alignment_failed"
    assert result.wav_path is None and not (result.run_dir / "final" / ".complete").exists()


def test_runtime_crash_retains_verified_successful_prefix(tmp_path):
    request, _ = write_audio_request(tmp_path, segments=[
        {"id": "a", "audio": "真人录音.wav", "text": "甲。", "start_frame": 0, "end_frame": 100},
        {"id": "b", "audio": "真人录音.wav", "text": "乙。", "start_frame": 100, "end_frame": 200},
        {"id": "c", "audio": "真人录音.wav", "text": "丙。", "start_frame": 200, "end_frame": 300},
    ])
    with pytest.raises(RenderError, match="model-stage crash") as caught:
        run_alignment(request, tmp_path / "run", FakeExternalAligner(runtime_after=1))
    assert caught.value.run_dir == (tmp_path / "run").resolve()
    assert load_unit(tmp_path / "run" / "units" / "0001" / "unit.json").text == "甲。"
    report = read_json(tmp_path / "run" / "report.json")
    assert report["status"] == "failed"
    assert [item["status"] for item in report["segments"]] == ["ok", "pending", "pending"]
    assert not (tmp_path / "run" / "final" / ".complete").exists()


@pytest.mark.parametrize("entries", [
    [], [{"segment_id": "真人旁白", "status": "ok"}] * 2,
    [{"segment_id": "unexpected", "status": "ok"}],
    [{"segment_id": "真人旁白", "status": "synthesis_failed"}],
    "not-an-array", ["not-an-object"],
])
def test_missing_duplicate_unexpected_or_malformed_stage_results_fail(tmp_path, entries):
    request, _ = write_audio_request(tmp_path)

    def malformed_result(task):
        return {"runtime_error": None, "segments": entries}

    with pytest.raises(RenderError) as caught:
        run_alignment(request, tmp_path / "run", malformed_result)
    assert caught.value.run_dir is not None
    assert read_json(tmp_path / "run" / "report.json")["status"] == "failed"
    assert not (tmp_path / "run" / "final" / ".complete").exists()


def test_missing_alignment_file_is_io_run_failure_not_isolated_validation(tmp_path):
    request, _ = write_audio_request(tmp_path)

    def claimed_success_without_file(task):
        return {"runtime_error": None, "segments": [{"segment_id": "真人旁白", "status": "ok"}]}

    with pytest.raises(RenderError) as caught:
        run_alignment(request, tmp_path / "run", claimed_success_without_file)
    assert caught.value.run_dir is not None
    assert read_json(tmp_path / "run" / "report.json")["status"] == "failed"
    assert not (tmp_path / "run" / "final" / ".complete").exists()


def test_report_write_io_failure_cannot_leave_final_success(tmp_path, monkeypatch):
    request, _ = write_audio_request(tmp_path)
    original_write = external_audio.write_json

    def failing_final_report(path, payload):
        if Path(path).name == "report.json" and payload.get("status") == "complete":
            raise OSError("controlled report storage failure")
        return original_write(path, payload)

    monkeypatch.setattr(external_audio, "write_json", failing_final_report)
    with pytest.raises(RenderError, match="report storage failure"):
        run_alignment(request, tmp_path / "run", FakeExternalAligner())
    report = read_json(tmp_path / "run" / "report.json")
    assert report["status"] == "failed"
    assert not (tmp_path / "run" / "final" / ".complete").exists()
    assert load_unit(tmp_path / "run" / "units" / "0001" / "unit.json").text == "准确讲稿。"


def test_persistent_wrapped_report_io_failure_keeps_started_error_contract(tmp_path, monkeypatch):
    request, _ = write_audio_request(tmp_path)
    original_write = external_audio.write_json

    def failing_final_and_failed_reports(path, payload):
        if Path(path).name == "report.json" and payload.get("status") in ("complete", "failed"):
            try:
                raise OSError("controlled persistent report storage failure")
            except OSError as cause:
                raise ArtifactError("cannot write report") from cause
        return original_write(path, payload)

    monkeypatch.setattr(external_audio, "write_json", failing_final_and_failed_reports)
    with pytest.raises(RenderError) as caught:
        run_alignment(request, tmp_path / "run", FakeExternalAligner())
    assert caught.value.run_dir == (tmp_path / "run").resolve()
    assert not (tmp_path / "run" / "final" / ".complete").exists()


def test_source_mutation_after_preflight_is_rejected_before_models(tmp_path, monkeypatch):
    request, original = write_audio_request(tmp_path)
    original_load = external_audio.load_audio_request

    def loading_then_mutating(path):
        result = original_load(path)
        samples, rate, _ = read_wav(original)
        samples[0] = 10
        write_wav(original, samples, rate)
        return result

    monkeypatch.setattr(external_audio, "load_audio_request", loading_then_mutating)
    fake = FakeExternalAligner()
    with pytest.raises(RenderError, match="changed since preflight"):
        run_alignment(request, tmp_path / "run", fake)
    assert not fake.calls
    assert read_json(tmp_path / "run" / "report.json")["status"] == "failed"


@pytest.mark.parametrize("processing", ["preserve", "trim"])
def test_model_stage_cannot_mutate_owned_audio_snapshot(tmp_path, processing):
    request, original = write_audio_request(tmp_path, processing=processing)
    original_bytes = original.read_bytes()
    normal = FakeExternalAligner()

    def mutating_stage(task):
        result = normal(task)
        snapshot = Path(task["run_dir"]) / "segments" / (task["segments"][0]["safe_name"] + ".wav")
        samples, rate, _ = read_wav(snapshot)
        samples[0] += 1
        write_wav(snapshot, samples, rate)
        return result

    with pytest.raises(RenderError):
        run_alignment(request, tmp_path / "run", mutating_stage)
    assert read_json(tmp_path / "run" / "report.json")["status"] == "failed"
    assert not (tmp_path / "run" / "final" / ".complete").exists()
    assert original.read_bytes() == original_bytes


def test_existing_run_is_immutable(tmp_path):
    request, _ = write_audio_request(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("original")
    fake = FakeExternalAligner()
    with pytest.raises(RenderError, match="already exists") as caught:
        run_alignment(request, output, fake)
    assert caught.value.run_dir is None and not fake.calls
    assert sentinel.read_text() == "original"
