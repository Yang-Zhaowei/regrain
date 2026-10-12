"""CPU acceptance for reusable speech revisions; no GPU/model acceptance.

Every asset is generated under pytest's temporary directory. The immutable
real-model regression fixtures are neither modified nor used as model output.
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

from regrain import audio_assembly, production, render
from regrain.artifacts import ArtifactError, SpeechUnit, create_unit, load_unit
from regrain.captions import Caption, render_srt
from regrain.errors import RenderError
from regrain.postprocess import read_wav, write_wav
from regrain.production import assemble_speech, create_assembly_plan, import_render_run
from support import FakeAlignerEngine, FakeTTSEngine


@pytest.fixture(autouse=True)
def _restore_fake_model_loads():
    # Their constructors, including inherited constructors, increment the base
    # class counters used by test_render's preflight/model-lifecycle assertions.
    tts_loads, alignment_loads = FakeTTSEngine.loads, FakeAlignerEngine.loads
    try:
        yield
    finally:
        FakeTTSEngine.loads = tts_loads
        FakeAlignerEngine.loads = alignment_loads


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def _unit(root: Path, name: str, *, frames: int = 1001, rate: int = 24000,
          text: str = "准确旁白。", amplitude: int = 71) -> SpeechUnit:
    source = root / "inputs" / name
    source.mkdir(parents=True)
    audio = source / "voice.wav"
    alignment = source / "alignment.json"
    # Deterministic PCM makes preservation observable even for equal durations.
    samples = [((index * amplitude) % 10001) - 5000 for index in range(frames)]
    write_wav(audio, samples, rate)
    _write_json(alignment, [{"text": text.rstrip("。"), "start": 17 / rate,
                             "end": (frames - 13) / rate}])
    before = _tree_bytes(source)
    unit = create_unit(root / "assets" / name, audio_path=audio, alignment_path=alignment,
                       text=text, language="Chinese", provenance={"workflow": "cpu_fixture", "name": name})
    assert _tree_bytes(source) == before
    return unit


def _plan(path: Path, units: list[SpeechUnit], *, pauses: list[int] | None = None,
          ids: list[str] | None = None) -> Path:
    pauses = pauses or [0] * len(units)
    ids = ids or [f"unit-{index + 1}" for index in range(len(units))]
    _write_json(path, {"schema_version": 1, "units": [
        {"id": identity, "artifact": Path(os.path.relpath(unit.manifest_path, path.parent)).as_posix(),
         "sha256": unit.manifest_sha256, "pause_after_ms": pause}
        for identity, unit, pause in zip(ids, units, pauses)
    ]})
    return path


def _assert_final(result, units: list[SpeechUnit], pauses: list[int], ids: list[str]) -> dict:
    assert result.status == "complete"
    assert (result.run_dir / "final" / ".complete").read_text(encoding="utf-8") == "complete"
    report = _json(result.report_path)
    assert report["status"] == "complete"
    assert [item["segment_id"] for item in report["segments"]] == ids
    rate = units[0].sample_rate
    expected_samples, expected_captions, expected_timeline = [], [], []
    running = 0
    for identity, unit, pause in zip(ids, units, pauses):
        pause_frames = int((Decimal(pause) * rate / 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        expected_timeline.append({"segment_id": identity, "start_frame": running,
                                 "audio_frames": unit.frames, "pause_after_frames": pause_frames})
        expected_samples.extend(unit.samples)
        expected_samples.extend([0] * pause_frames)
        expected_captions.extend(Caption(caption.start + running / rate, caption.end + running / rate,
                                        caption.text) for caption in unit.local_captions)
        running += unit.frames + pause_frames
    assert read_wav(result.wav_path) == (expected_samples, rate, running)
    timeline = _json(result.timeline_path)
    assert timeline["sample_rate"] == rate
    assert timeline["total_frames"] == running
    assert timeline["duration_seconds"] == running / rate
    assert timeline["segments"] == expected_timeline
    assert result.srt_path.read_text(encoding="utf-8") == render_srt(expected_captions)
    assert all(type(item["start_frame"]) is int for item in timeline["segments"])
    return report


def _render_run(root: Path, name: str, count: int = 3) -> Path:
    script = root / (name + ".json")
    _write_json(script, {"language": "Chinese", "speaker": "Uncle_Fu", "segments": [
        {"id": f"unit-{index + 1}", "text": f"第{index + 1}段旁白。", "pause_after_ms": 3}
        for index in range(count)
    ]})
    run = root / name
    result = render.render_speech(script, run, tts_python="cpu-fake", alignment_python="cpu-fake",
                                  tts_model="cpu-fake", alignment_model="cpu-fake", max_segment_chars=200,
                                  _wav_task=FakeTTSEngine(), _align_task=FakeAlignerEngine())
    assert result.status == "complete"
    return run


def _run_units(run: Path) -> list[SpeechUnit]:
    return [load_unit(run / entry["unit_manifest"], expected_sha256=entry["unit_sha256"])
            for entry in _json(run / "report.json")["segments"]]


def _forbid_models(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("audio revision must not dispatch TTS or Alignment")
    monkeypatch.setattr(render, "_run_model_stage", forbidden)


def test_fifteen_unit_episode_pause_revision_reuses_audio_and_alignment(tmp_path, monkeypatch) -> None:
    run = _render_run(tmp_path, "episode", 15)
    units = _run_units(run)
    source_before = _tree_bytes(run)
    plan = create_assembly_plan(run, tmp_path / "revision.json")
    payload = _json(plan)
    pauses = [0, 1, 17, 125, 250] * 3
    for entry, pause in zip(payload["units"], pauses):
        entry["pause_after_ms"] = pause
    _write_json(plan, payload)
    plan_before = plan.read_bytes()
    _forbid_models(monkeypatch)
    revised = assemble_speech(plan, tmp_path / "revised")
    report = _assert_final(revised, units, pauses, [f"unit-{i + 1}" for i in range(15)])
    assert report["completed_stages"] == ["snapshot", "assemble"]
    assert _tree_bytes(run) == source_before
    assert plan.read_bytes() == plan_before


def test_render_final_is_assembled_from_the_verified_unit_snapshot(tmp_path, monkeypatch) -> None:
    original_create = render.create_unit

    def latest_verified_snapshot(output_dir, **kwargs):
        # Simulate a valid revision before the Unit's immutable snapshot boundary.
        # Final and future revisions must consume the same verified Unit data.
        samples, rate, _ = read_wav(kwargs["audio_path"])
        samples[0] += 1
        write_wav(kwargs["audio_path"], samples, rate)
        alignment = json.loads(Path(kwargs["alignment_path"]).read_text(encoding="utf-8"))
        alignment[0]["start"] += 0.002
        _write_json(Path(kwargs["alignment_path"]), alignment)
        return original_create(output_dir, **kwargs)

    monkeypatch.setattr(render, "create_unit", latest_verified_snapshot)
    run = _render_run(tmp_path, "rendered", 1)
    unit = _run_units(run)[0]
    result = render.RenderResult("complete", run, run / "report.json", run / "final" / "final.wav",
                                run / "final" / "final.srt", run / "final" / "timeline.json", [])
    _assert_final(result, [unit], [3], ["unit-1"])


def test_alignment_stage_cannot_mutate_pinned_tts_wav(tmp_path) -> None:
    script = tmp_path / "speech.json"
    _write_json(script, {"language": "Chinese", "speaker": "Uncle_Fu",
                         "segments": [{"id": "spoken", "text": "不可修改的语音。"}]})
    script_before = script.read_bytes()
    aligner = FakeAlignerEngine()

    def mutating_aligner(task):
        result = aligner(task)
        segment = task["segments"][0]
        audio = Path(task["run_dir"]) / "segments" / (segment["safe_name"] + ".wav")
        samples, rate, _ = read_wav(audio)
        samples[0] += 1
        write_wav(audio, samples, rate)
        return result

    output = tmp_path / "failed"
    with pytest.raises(RenderError, match="changed immutable TTS audio") as excinfo:
        render.render_speech(script, output, tts_python="cpu-fake", alignment_python="cpu-fake",
                             tts_model="cpu-fake", alignment_model="cpu-fake", max_segment_chars=200,
                             _wav_task=FakeTTSEngine(), _align_task=mutating_aligner)
    assert excinfo.value.run_dir == output
    assert _json(output / "report.json")["status"] == "failed"
    assert not (output / "final" / ".complete").exists()
    assert not list((output / "units").glob("*/.complete"))
    assert script.read_bytes() == script_before


@pytest.mark.parametrize("replacement_indexes", [[1], [0, 2]])
def test_cross_run_replacements_change_duration_and_preserve_other_pcm(
    tmp_path, monkeypatch, replacement_indexes,
) -> None:
    run = _render_run(tmp_path, "original")
    originals = _run_units(run)
    replacements = [_unit(tmp_path / "other-run", f"voice-{i}", frames=2003 + i * 409,
                          text=originals[i].text, amplitude=213 + i) for i in replacement_indexes]
    inputs_before = _tree_bytes(tmp_path / "other-run")
    source_before = _tree_bytes(run)
    plan = create_assembly_plan(run, tmp_path / "replace.json")
    payload = _json(plan)
    expected = list(originals)
    for index, unit in zip(replacement_indexes, replacements):
        payload["units"][index].update(artifact=Path(os.path.relpath(unit.manifest_path, plan.parent)).as_posix(),
                                        sha256=unit.manifest_sha256)
        expected[index] = unit
    _write_json(plan, payload)
    _forbid_models(monkeypatch)
    revised = assemble_speech(plan, tmp_path / "replaced")
    report = _assert_final(revised, expected, [3] * 3, ["unit-1", "unit-2", "unit-3"])
    snapshots = _run_units(revised.run_dir)
    for index, snapshot in enumerate(snapshots):
        assert snapshot.samples == expected[index].samples
        assert snapshot.text == originals[index].text
        assert snapshot.alignment_path.read_bytes() == expected[index].alignment_path.read_bytes()
        assert report["segments"][index]["source"]["sha256"] == expected[index].manifest_sha256
    assert _tree_bytes(run) == source_before
    assert _tree_bytes(tmp_path / "other-run") == inputs_before


def test_assembled_run_supports_sequential_revision_after_source_moves(tmp_path, monkeypatch) -> None:
    unit = _unit(tmp_path / "source", "voice")
    plan = _plan(tmp_path / "first.json", [unit], pauses=[7], ids=["spoken"])
    _forbid_models(monkeypatch)
    first = assemble_speech(plan, tmp_path / "first-run")
    assert _run_units(first.run_dir)[0].audio_path.read_bytes() == unit.audio_path.read_bytes()
    (tmp_path / "source").rename(tmp_path / "moved-source")
    # The run's Unit snapshots remain independently verifiable and portable.
    first.run_dir.rename(tmp_path / "moved-first-run")
    moved_run = tmp_path / "moved-first-run"
    local_unit = _run_units(moved_run)[0]
    next_plan = create_assembly_plan(moved_run, tmp_path / "second.json")
    payload = _json(next_plan)
    payload["units"][0]["pause_after_ms"] = 41
    _write_json(next_plan, payload)
    second = assemble_speech(next_plan, tmp_path / "second-run")
    _assert_final(second, [local_unit], [41], ["spoken"])
    assert local_unit.samples == unit.samples


def test_explicit_repair_reuses_good_units_from_an_incomplete_render(tmp_path, monkeypatch) -> None:
    script = tmp_path / "speech.json"
    segments = [{"id": f"unit-{index + 1}", "text": f"第{index + 1}段旁白。"} for index in range(3)]
    _write_json(script, {"language": "Chinese", "speaker": "Uncle_Fu", "segments": segments})
    run = tmp_path / "incomplete"
    result = render.render_speech(script, run, tts_python="cpu-fake", alignment_python="cpu-fake",
                                  tts_model="cpu-fake", alignment_model="cpu-fake", max_segment_chars=200,
                                  _wav_task=FakeTTSEngine(), _align_task=FakeAlignerEngine(fail_ids={"unit-2"}))
    assert result.status == "incomplete"
    assert not (run / "final" / ".complete").exists()
    before = _tree_bytes(run)
    with pytest.raises(ArtifactError, match="no reusable Unit"):
        create_assembly_plan(run, tmp_path / "cannot-pretend-complete.json")
    assert not (tmp_path / "cannot-pretend-complete.json").exists()
    entries = _json(run / "report.json")["segments"]
    first = load_unit(run / entries[0]["unit_manifest"], expected_sha256=entries[0]["unit_sha256"])
    last = load_unit(run / entries[2]["unit_manifest"], expected_sha256=entries[2]["unit_sha256"])
    replacement = _unit(tmp_path / "replacement", "voice", text=segments[1]["text"])
    plan = _plan(tmp_path / "repair.json", [first, replacement, last])
    _forbid_models(monkeypatch)
    repaired = assemble_speech(plan, tmp_path / "repaired")
    _assert_final(repaired, [first, replacement, last], [0, 0, 0], ["unit-1", "unit-2", "unit-3"])
    assert _tree_bytes(run) == before


def test_reordered_units_and_44100_hz_pauses_use_integer_frame_offsets(tmp_path) -> None:
    units = [_unit(tmp_path, "one", rate=44100, frames=1003, text="第一段。"),
             _unit(tmp_path, "two", rate=44100, frames=1507, text="第二段。")]
    plan = _plan(tmp_path / "assembly.json", list(reversed(units)), pauses=[5, 1], ids=["second", "first"])
    result = assemble_speech(plan, tmp_path / "assembled")
    _assert_final(result, list(reversed(units)), [5, 1], ["second", "first"])
    timeline = _json(result.timeline_path)
    assert timeline["segments"][0]["pause_after_frames"] == 221
    assert timeline["segments"][1]["start_frame"] == 1507 + 221
    assert timeline["segments"][1]["pause_after_frames"] == 44


@pytest.mark.parametrize("target", ["manifest", "audio", "alignment", "marker"])
def test_pinned_unit_tampering_fails_before_creating_output(tmp_path, target) -> None:
    unit = _unit(tmp_path, "voice")
    plan = _plan(tmp_path / "assembly.json", [unit])
    if target == "manifest":
        payload = _json(unit.manifest_path)
        payload["provenance"]["name"] = "changed"
        _write_json(unit.manifest_path, payload)
    elif target == "audio":
        changed = list(unit.samples)
        changed[0] += 1
        write_wav(unit.audio_path, changed, unit.sample_rate)
    elif target == "alignment":
        unit.alignment_path.write_text("[]\n", encoding="utf-8")
    else:
        (unit.manifest_path.parent / ".complete").write_text("0" * 64, encoding="ascii")
    with pytest.raises(RenderError):
        assemble_speech(plan, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_sample_rate_mismatch_requires_explicit_realignment_before_output(tmp_path) -> None:
    units = [_unit(tmp_path, "first", rate=24000), _unit(tmp_path, "second", rate=44100)]
    plan = _plan(tmp_path / "assembly.json", units)
    with pytest.raises(RenderError, match="sample rate mismatch"):
        assemble_speech(plan, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("mutation", ["unknown_top", "unknown_unit", "bool_version", "bool_pause",
                                      "negative_pause", "fractional_pause", "duplicate_id", "empty_units"])
def test_invalid_assembly_contract_is_preflight_failure(tmp_path, mutation) -> None:
    unit = _unit(tmp_path, "voice")
    plan = _plan(tmp_path / "assembly.json", [unit])
    payload = _json(plan)
    if mutation == "unknown_top":
        payload["ignored"] = 1
    elif mutation == "unknown_unit":
        payload["units"][0]["ignored"] = 1
    elif mutation == "bool_version":
        payload["schema_version"] = True
    elif mutation == "bool_pause":
        payload["units"][0]["pause_after_ms"] = False
    elif mutation == "negative_pause":
        payload["units"][0]["pause_after_ms"] = -1
    elif mutation == "fractional_pause":
        payload["units"][0]["pause_after_ms"] = 0.5
    elif mutation == "duplicate_id":
        payload["units"].append(dict(payload["units"][0]))
    else:
        payload["units"] = []
    _write_json(plan, payload)
    with pytest.raises(RenderError):
        assemble_speech(plan, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("reference", ["/absolute/unit.json", "C:/host/unit.json", "C:relative/unit.json",
                                      "https://host/unit.json", "\\\\server\\share\\unit.json"])
def test_authored_asset_references_reject_host_and_uri_paths(tmp_path, reference) -> None:
    unit = _unit(tmp_path, "voice")
    plan = _plan(tmp_path / "assembly.json", [unit])
    payload = _json(plan)
    payload["units"][0]["artifact"] = reference
    _write_json(plan, payload)
    with pytest.raises(RenderError, match="relative"):
        assemble_speech(plan, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_existing_output_and_plan_are_never_overwritten(tmp_path) -> None:
    run = _render_run(tmp_path, "rendered", 1)
    plan = create_assembly_plan(run, tmp_path / "assembly.json")
    plan_before = plan.read_bytes()
    with pytest.raises(ArtifactError, match="already exists"):
        create_assembly_plan(run, plan)
    assert plan.read_bytes() == plan_before
    output = tmp_path / "occupied"
    output.mkdir()
    (output / "keep.txt").write_text("owner data", encoding="utf-8")
    before = _tree_bytes(output)
    with pytest.raises(RenderError, match="already exists"):
        assemble_speech(plan, output)
    assert _tree_bytes(output) == before


@pytest.mark.parametrize("mutation", ["missing_pin", "wrong_pin", "transcript", "missing_marker", "order"])
def test_plan_export_requires_matching_complete_unit_records(tmp_path, mutation) -> None:
    run = _render_run(tmp_path, "rendered", 2)
    report = _json(run / "report.json")
    request = _json(run / "request.json")
    if mutation == "missing_pin":
        report["segments"][0].pop("unit_sha256")
        _write_json(run / "report.json", report)
    elif mutation == "wrong_pin":
        report["segments"][0]["unit_sha256"] = "0" * 64
        _write_json(run / "report.json", report)
    elif mutation == "transcript":
        request["segments"][0]["text"] = "不同的讲稿。"
        _write_json(run / "request.json", request)
    elif mutation == "missing_marker":
        (run / report["segments"][0]["unit_manifest"]).with_name(".complete").unlink()
    else:
        report["segments"].reverse()
        _write_json(run / "report.json", report)
    before = _tree_bytes(run)
    destination = tmp_path / "rejected-plan.json"
    with pytest.raises(ArtifactError):
        create_assembly_plan(run, destination)
    assert not destination.exists()
    assert _tree_bytes(run) == before


@pytest.mark.parametrize("record", ["request", "report"])
def test_malformed_run_records_are_reported_as_preflight_errors(tmp_path, record) -> None:
    run = _render_run(tmp_path, "rendered", 1)
    path = run / (record + ".json")
    payload = _json(path)
    payload["segments"] = [1]
    _write_json(path, payload)
    before = _tree_bytes(run)
    with pytest.raises(ArtifactError, match="objects"):
        create_assembly_plan(run, tmp_path / "rejected-plan.json")
    assert not (tmp_path / "rejected-plan.json").exists()
    assert _tree_bytes(run) == before


def test_importing_an_assembly_run_gives_explicit_preflight_error(tmp_path) -> None:
    unit = _unit(tmp_path, "voice")
    plan = _plan(tmp_path / "assembly.json", [unit])
    result = assemble_speech(plan, tmp_path / "assembled")
    before = _tree_bytes(result.run_dir)
    with pytest.raises(ArtifactError, match="render request"):
        import_render_run(result.run_dir, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
    assert _tree_bytes(result.run_dir) == before


def test_worker_nonzero_exit_preserves_prefix_without_reporting_success(tmp_path) -> None:
    stage = r"""
import json
import sys
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
Path(spec['manifest']).write_text(json.dumps({
    'runtime_error': None, 'segments': [{'segment_id': 'verified', 'status': 'ok'}],
}), encoding='utf-8')
raise SystemExit(7)
"""
    result = render._run_model_stage(sys.executable, stage, ("cpu-fake", {"run_dir": str(tmp_path)}))
    assert result["segments"] == [{"segment_id": "verified", "status": "ok"}]
    assert "exit 7" in result["runtime_error"]


def test_source_changed_between_preflight_and_snapshot_is_failed_run(tmp_path, monkeypatch) -> None:
    unit = _unit(tmp_path, "voice")
    plan = _plan(tmp_path / "assembly.json", [unit])
    original_snapshot = production._snapshot_unit_files

    def changed_snapshot(source, destination):
        altered, _, _ = read_wav(source.audio_path)
        altered[-1] += 1
        write_wav(source.audio_path, altered, source.sample_rate)
        return original_snapshot(source, destination)

    monkeypatch.setattr(production, "_snapshot_unit_files", changed_snapshot)
    output = tmp_path / "failed"
    with pytest.raises(RenderError, match="SHA-256"):
        assemble_speech(plan, output)
    assert _json(output / "report.json")["status"] == "failed"
    assert not (output / "final" / ".complete").exists()


@pytest.mark.parametrize("fault", ["publish", "complete_report", "marker", "marker_after_write", "corrupt_publish",
                                  "valid_wrong_pcm_publish", "valid_wrong_timeline_publish"])
def test_assembly_publish_report_and_marker_faults_never_complete(tmp_path, monkeypatch, fault) -> None:
    unit = _unit(tmp_path, "voice")
    plan = _plan(tmp_path / "assembly.json", [unit], pauses=[7])
    output = tmp_path / "failed"
    original_publish = audio_assembly._publish
    original_report = production.write_json
    from regrain import artifacts
    original_atomic_write = artifacts._atomic_write

    if fault in {"publish", "corrupt_publish", "valid_wrong_pcm_publish", "valid_wrong_timeline_publish"}:
        def publish(source, destination):
            if fault == "publish" and destination.name == "final.srt":
                raise OSError("simulated publication failure")
            original_publish(source, destination)
            if fault == "corrupt_publish" and destination.name == "final.wav":
                destination.write_bytes(b"truncated publication")
            elif fault == "valid_wrong_pcm_publish" and destination.name == "final.wav":
                samples, rate, _ = read_wav(destination)
                samples[0] += 1
                write_wav(destination, samples, rate)
            elif fault == "valid_wrong_timeline_publish" and destination.name == "timeline.json":
                timeline = _json(destination)
                timeline["segments"][0]["audio_frames"] += 1
                timeline["segments"][0]["pause_after_frames"] -= 1
                _write_json(destination, timeline)
        monkeypatch.setattr(audio_assembly, "_publish", publish)
    elif fault == "complete_report":
        def write_report(path, payload):
            if Path(path).name == "report.json" and payload.get("status") == "complete":
                raise OSError("simulated report failure")
            return original_report(path, payload)
        monkeypatch.setattr(production, "write_json", write_report)
    else:
        def write_marker(path, data):
            if path == output / "final" / ".complete":
                if fault == "marker_after_write":
                    original_atomic_write(path, data)
                raise OSError("simulated marker failure")
            return original_atomic_write(path, data)
        monkeypatch.setattr(artifacts, "_atomic_write", write_marker)

    with pytest.raises(RenderError) as excinfo:
        assemble_speech(plan, output)
    assert excinfo.value.run_dir == output
    assert not (output / "final" / ".complete").exists()
    assert _json(output / "report.json")["status"] == "failed"
    # Retrying always gets a distinct run and never rewrites failed evidence.
    with pytest.raises(RenderError, match="already exists"):
        assemble_speech(plan, output)


def _legacy_run(root: Path) -> tuple[Path, list[SpeechUnit]]:
    units = [_unit(root, "first", text="第一段旁白。"), _unit(root, "second", frames=1213, text="第二段旁白。")]
    run = root / "legacy"
    segments = [{"id": f"unit-{index + 1}", "text": unit.text, "pause_after_ms": 9}
                for index, unit in enumerate(units)]
    _write_json(run / "request.json", {"language": "Chinese", "speaker": "Uncle_Fu", "segments": segments})
    _write_json(run / "report.json", {"status": "complete", "segments": [
        {"segment_id": seg["id"], "text": seg["text"], "status": "ok"} for seg in segments
    ]})
    (run / "segments").mkdir()
    for seg, unit in zip(segments, units):
        name = render._safe_name(seg["id"])
        (run / "segments" / (name + ".cleaned.wav")).write_bytes(unit.audio_path.read_bytes())
        (run / "segments" / (name + ".alignment.adjusted.json")).write_bytes(unit.alignment_path.read_bytes())
    cleaned = [audio_assembly._CleanedSegment(seg["id"], render._safe_name(seg["id"]),
                unit.samples, unit.sample_rate, unit.frames, seg["pause_after_ms"], unit.local_captions)
               for seg, unit in zip(segments, units)]
    final = audio_assembly.assemble_audio(cleaned, run)
    (final / ".complete").write_text("complete", encoding="utf-8")
    return run, units


def test_legacy_import_preserves_inputs_and_enables_pinned_assembly(tmp_path, monkeypatch) -> None:
    legacy, units = _legacy_run(tmp_path)
    before = _tree_bytes(legacy)
    _forbid_models(monkeypatch)
    imported = import_render_run(legacy, tmp_path / "imported")
    assert _json(imported / "report.json")["status"] == "complete"
    assert (imported / "final" / ".complete").read_text(encoding="utf-8") == "complete"
    assert all(unit.provenance["integrity_origin"] == "bytes_observed_at_import" for unit in _run_units(imported))
    plan = create_assembly_plan(imported, tmp_path / "assembly.json")
    result = assemble_speech(plan, tmp_path / "delivered")
    _assert_final(result, units, [9, 9], ["unit-1", "unit-2"])
    assert _tree_bytes(legacy) == before


def test_invalid_legacy_alignment_cannot_finish_import(tmp_path) -> None:
    legacy, _ = _legacy_run(tmp_path)
    alignment = legacy / "segments" / (render._safe_name("unit-2") + ".alignment.adjusted.json")
    alignment.write_text("[]\n", encoding="utf-8")
    before = _tree_bytes(legacy)
    destination = tmp_path / "imported"
    with pytest.raises(RenderError) as excinfo:
        import_render_run(legacy, destination)
    assert excinfo.value.run_dir == destination
    assert _json(destination / "report.json")["status"] == "failed"
    assert _tree_bytes(legacy) == before
    assert not (destination / "final" / ".complete").exists()


@pytest.mark.parametrize("artifact", ["final.wav", "final.srt", "timeline.json"])
@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_legacy_import_requires_original_final_consistent_with_units(tmp_path, artifact, mutation) -> None:
    legacy, _ = _legacy_run(tmp_path)
    source = legacy / "final" / artifact
    if mutation == "missing":
        source.unlink()
    elif artifact == "final.wav":
        samples, rate, _ = read_wav(source)
        samples[0] += 1
        write_wav(source, samples, rate)
    elif artifact == "final.srt":
        source.write_text(source.read_text(encoding="utf-8").replace("第一", "错误"), encoding="utf-8")
    else:
        timeline = _json(source)
        timeline["segments"][1]["start_frame"] += 1
        _write_json(source, timeline)
    before = _tree_bytes(legacy)
    destination = tmp_path / "imported"
    with pytest.raises(RenderError) as excinfo:
        import_render_run(legacy, destination)
    assert excinfo.value.run_dir == destination
    assert _json(destination / "report.json")["status"] == "failed"
    assert not (destination / "final" / ".complete").exists()
    assert _tree_bytes(legacy) == before
