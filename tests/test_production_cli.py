"""CPU CLI contracts for reusable speech production and external alignment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from regrain import cli, external_audio, render
from regrain.artifacts import read_json, write_json
from regrain.audio_assembly import _CleanedSegment, assemble_audio
from regrain.captions import Caption
from regrain.postprocess import read_wav, write_wav
from regrain.production import create_assembly_plan
from regrain.render import RenderError
from support import FakeExternalAligner, run_alignment, write_audio_request


@pytest.fixture(autouse=True)
def clear_runtime_environment(monkeypatch):
    for prefix in ("REGRAIN_", "MEDIA_PIPELINE_", "MEDIA_"):
        for suffix in ("TTS_PYTHON", "TTS_MODEL", "ALIGNMENT_PYTHON", "ALIGNMENT_MODEL"):
            monkeypatch.delenv(prefix + suffix, raising=False)


def invoke(capsys, argv):
    code = cli.main(argv)
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    return code, payload


def no_call(*args, **kwargs):
    pytest.fail("this command must not start a model stage")


def inject_aligner(monkeypatch, fake):
    def align_with_cpu_fake(request, output, **config):
        return external_audio.align_speech(request, output, _align_task=fake, **config)
    monkeypatch.setattr(cli, "align_speech", align_with_cpu_fake)


def build_legacy_run(tmp_path: Path) -> Path:
    run = tmp_path / "legacy"
    (run / "segments").mkdir(parents=True)
    (run / "final").mkdir()
    segment_id, text = "历史片段", "保留讲稿。"
    name = render._safe_name(segment_id)
    write_json(run / "request.json", {"language": "Chinese", "speaker": "Uncle_Fu",
               "segments": [{"id": segment_id, "text": text, "pause_after_ms": 125}]})
    write_json(run / "report.json", {"status": "complete", "segments": [
        {"segment_id": segment_id, "text": text, "status": "ok"},
    ]})
    write_wav(run / "segments" / (name + ".cleaned.wav"), [1000] * 100, 100)
    write_json(run / "segments" / (name + ".alignment.adjusted.json"), [
        {"text": "保留讲稿", "start": 0.1, "end": 0.9},
    ])
    assemble_audio([_CleanedSegment(segment_id, name, [1000] * 100, 100, 100, 125,
                                   [Caption(0.1, 0.9, text)])], run)
    (run / "final" / ".complete").write_text("complete", encoding="utf-8")
    return run


@pytest.mark.parametrize("command", ["align", "assemble", "plan", "import-run"])
def test_new_commands_require_output_with_structured_usage_error(tmp_path, capsys, command):
    code, payload = invoke(capsys, ["speech", command, str(tmp_path / "input.json")])
    assert code == 2 and payload["status"] == "error" and payload["command"] is None
    assert "--output" in payload["error"]


@pytest.mark.parametrize("argv", [
    ["speech", "align", "request.json", "--output", "run", "--tts-python", "python"],
    ["speech", "align", "request.json", "--out", "run"],
    ["speech", "assemble", "plan.json", "--output", "run", "--device", "cpu"],
    ["speech", "plan", "run", "--output", "plan.json", "--alignment-model", "model"],
])
def test_unknown_or_abbreviated_arguments_are_rejected(capsys, argv):
    code, payload = invoke(capsys, argv)
    assert code == 2 and payload["status"] == "error"


def test_align_needs_only_new_alignment_configuration(tmp_path, capsys, monkeypatch):
    request, _ = write_audio_request(tmp_path)
    monkeypatch.setenv("REGRAIN_ALIGNMENT_PYTHON", "cpu-alignment-python")
    monkeypatch.setenv("REGRAIN_ALIGNMENT_MODEL", "cpu-alignment-model")
    monkeypatch.setattr(render, "_make_tts_task", no_call)
    fake = FakeExternalAligner()
    inject_aligner(monkeypatch, fake)
    code, payload = invoke(capsys, ["speech", "align", str(request), "--output", str(tmp_path / "run"), "--device", "cpu"])
    assert code == 0 and payload["status"] == "complete" and payload["command"] == "align"
    assert len(fake.calls) == 1
    assert payload["segments"][0]["segment_id"] == "真人旁白"
    assert all(Path(payload[key]).is_file() for key in ("wav_path", "srt_path", "timeline_path", "report_path"))
    config = read_json(tmp_path / "run" / "report.json")["config"]
    assert config["alignment_python"] == "cpu-alignment-python"
    assert config["alignment_model"] == "cpu-alignment-model"


def test_alignment_flags_override_environment(tmp_path, capsys, monkeypatch):
    request, _ = write_audio_request(tmp_path)
    monkeypatch.setenv("REGRAIN_ALIGNMENT_PYTHON", "environment-python")
    monkeypatch.setenv("REGRAIN_ALIGNMENT_MODEL", "environment-model")
    received = []

    def recording_align(request_path, output, **config):
        received.append(config)
        return external_audio.align_speech(request_path, output, _align_task=FakeExternalAligner(), **config)

    monkeypatch.setattr(cli, "align_speech", recording_align)
    code, _ = invoke(capsys, ["speech", "align", str(request), "--output", str(tmp_path / "run"),
                            "--alignment-python", "flag-python", "--alignment-model", "flag-model", "--device", "cpu"])
    assert code == 0
    assert len(received) == 1
    prepared = received[0].pop("_prepared")
    assert all(win.samples is None for win in prepared[1])
    assert received == [{"alignment_python": "flag-python", "alignment_model": "flag-model", "device": "cpu"}]


@pytest.mark.parametrize("prefix", ["MEDIA_PIPELINE_", "MEDIA_"])
def test_old_alignment_environment_aliases_are_not_accepted(tmp_path, capsys, monkeypatch, prefix):
    request, _ = write_audio_request(tmp_path)
    monkeypatch.setenv(prefix + "ALIGNMENT_PYTHON", "old-python")
    monkeypatch.setenv(prefix + "ALIGNMENT_MODEL", "old-model")
    monkeypatch.setattr(cli, "align_speech", no_call)
    code, payload = invoke(capsys, ["speech", "align", str(request), "--output", str(tmp_path / "run")])
    assert code == 2 and payload["status"] == "error"
    assert "REGRAIN_ALIGNMENT_PYTHON" in payload["error"]
    assert not (tmp_path / "run").exists()


def test_tts_configuration_does_not_satisfy_alignment_configuration(tmp_path, capsys, monkeypatch):
    request, _ = write_audio_request(tmp_path)
    monkeypatch.setenv("REGRAIN_TTS_PYTHON", "tts-python")
    monkeypatch.setenv("REGRAIN_TTS_MODEL", "tts-model")
    monkeypatch.setattr(cli, "align_speech", no_call)
    code, payload = invoke(capsys, ["speech", "align", str(request), "--output", str(tmp_path / "run")])
    assert code == 2 and "REGRAIN_ALIGNMENT_PYTHON" in payload["error"]


@pytest.mark.parametrize("broken", ["{", '{"schema_version":1,"language":"Chinese","segments":[]}',
                                    '{"schema_version":1,"schema_version":1,"language":"Chinese","segments":[]}'])
def test_alignment_validation_precedes_runtime_configuration(tmp_path, capsys, monkeypatch, broken):
    request = tmp_path / "bad.json"
    request.write_text(broken, encoding="utf-8")
    monkeypatch.setattr(cli, "align_speech", no_call)
    code, payload = invoke(capsys, ["speech", "align", str(request), "--output", str(tmp_path / "run")])
    assert code == 3 and payload["status"] == "validation_failed"
    assert not (tmp_path / "run").exists()


def test_alignment_incomplete_uses_exit_four_and_preserves_report(tmp_path, capsys, monkeypatch):
    request, _ = write_audio_request(tmp_path)
    inject_aligner(monkeypatch, FakeExternalAligner(fail_ids={"真人旁白"}))
    code, payload = invoke(capsys, ["speech", "align", str(request), "--output", str(tmp_path / "run"),
                                 "--alignment-python", "fake-python", "--alignment-model", "fake-model"])
    assert code == 4 and payload["status"] == "incomplete"
    assert payload["wav_path"] is None and payload["srt_path"] is None and payload["timeline_path"] is None
    assert payload["segments"][0]["status"] == "alignment_failed"
    assert Path(payload["report_path"]).is_file()
    assert not (tmp_path / "run" / "final" / ".complete").exists()


def test_alignment_started_runtime_failure_uses_exit_five(tmp_path, capsys, monkeypatch):
    request, _ = write_audio_request(tmp_path)
    inject_aligner(monkeypatch, FakeExternalAligner(runtime_after=0))
    code, payload = invoke(capsys, ["speech", "align", str(request), "--output", str(tmp_path / "run"),
                                 "--alignment-python", "fake-python", "--alignment-model", "fake-model"])
    assert code == 5 and payload["status"] == "failed"
    assert payload["run_dir"] == str((tmp_path / "run").resolve())
    assert read_json(payload["report_path"])["status"] == "failed"
    assert payload["wav_path"] is None


def test_existing_alignment_output_is_operation_error_exit_two(tmp_path, capsys, monkeypatch):
    request, _ = write_audio_request(tmp_path)
    (tmp_path / "run").mkdir()
    inject_aligner(monkeypatch, FakeExternalAligner())
    code, payload = invoke(capsys, ["speech", "align", str(request), "--output", str(tmp_path / "run"),
                                 "--alignment-python", "fake-python", "--alignment-model", "fake-model"])
    assert code == 2 and payload["status"] == "error" and payload["run_dir"] is None


def test_plan_and_assemble_are_model_free_end_to_end(tmp_path, capsys, monkeypatch):
    request, original = write_audio_request(tmp_path)
    source = run_alignment(request, tmp_path / "source", FakeExternalAligner())
    monkeypatch.setattr(cli, "align_speech", no_call)
    monkeypatch.setattr(cli, "render_speech", no_call)
    monkeypatch.setattr(render, "_make_tts_task", no_call)
    monkeypatch.setattr(render, "_make_align_task", no_call)
    plan = tmp_path / "assembly.json"
    code, payload = invoke(capsys, ["speech", "plan", str(source.run_dir), "--output", str(plan)])
    assert code == 0 and payload["status"] == "complete" and payload["plan_path"] == str(plan.resolve())
    data = read_json(plan)
    data["units"][0]["pause_after_ms"] = 25
    write_json(plan, data)
    code, payload = invoke(capsys, ["speech", "assemble", str(plan), "--output", str(tmp_path / "revision")])
    assert code == 0 and payload["command"] == "assemble" and payload["status"] == "complete"
    samples, rate, frames = read_wav(original)
    assert read_wav(payload["wav_path"]) == (samples + [0] * 3, rate, frames + 3)
    assert read_json(payload["timeline_path"])["segments"][0]["pause_after_frames"] == 3


def test_invalid_assembly_plan_is_validation_exit_three(tmp_path, capsys):
    plan = tmp_path / "bad-plan.json"
    write_json(plan, {"schema_version": True, "units": []})
    code, payload = invoke(capsys, ["speech", "assemble", str(plan), "--output", str(tmp_path / "run")])
    assert code == 3 and payload["status"] == "validation_failed"
    assert not (tmp_path / "run").exists()


def test_tampered_assembly_pin_is_validation_exit_three(tmp_path, capsys):
    request, _ = write_audio_request(tmp_path)
    source = run_alignment(request, tmp_path / "source", FakeExternalAligner())
    plan = create_assembly_plan(source.run_dir, tmp_path / "plan.json")
    payload = read_json(plan)
    payload["units"][0]["sha256"] = "0" * 64
    write_json(plan, payload)
    code, result = invoke(capsys, ["speech", "assemble", str(plan), "--output", str(tmp_path / "run")])
    assert code == 3 and result["status"] == "validation_failed"
    assert not (tmp_path / "run").exists()


def test_assembly_started_failure_is_exit_five(tmp_path, capsys, monkeypatch):
    plan = tmp_path / "plan.json"
    run = tmp_path / "run"
    run.mkdir()
    write_json(run / "report.json", {"status": "failed"})

    def started_failure(*args, **kwargs):
        raise RenderError("controlled assembly write failure", run_dir=run)

    monkeypatch.setattr(cli, "assemble_speech", started_failure)
    code, payload = invoke(capsys, ["speech", "assemble", str(plan), "--output", str(run)])
    assert code == 5 and payload["status"] == "failed"
    assert payload["report_path"] == str((run / "report.json").resolve())


def test_import_run_plan_assemble_round_trip_keeps_legacy_immutable(tmp_path, capsys, monkeypatch):
    legacy = build_legacy_run(tmp_path)
    before = {path.relative_to(legacy): path.read_bytes() for path in legacy.rglob("*") if path.is_file()}
    monkeypatch.setattr(cli, "align_speech", no_call)
    monkeypatch.setattr(cli, "render_speech", no_call)
    imported = tmp_path / "imported"
    code, payload = invoke(capsys, ["speech", "import-run", str(legacy), "--output", str(imported)])
    assert code == 0, payload
    assert payload["command"] == "import-run" and payload["status"] == "complete"
    assert payload["run_dir"] == str(imported.resolve())
    plan = tmp_path / "plan.json"
    code, _ = invoke(capsys, ["speech", "plan", str(imported), "--output", str(plan)])
    assert code == 0
    code, payload = invoke(capsys, ["speech", "assemble", str(plan), "--output", str(tmp_path / "rebuilt")])
    assert code == 0 and read_wav(payload["wav_path"])[2] == 113  # 125 ms at 100 Hz rounds half up.
    after = {path.relative_to(legacy): path.read_bytes() for path in legacy.rglob("*") if path.is_file()}
    assert after == before


def test_started_legacy_import_failure_uses_exit_five(tmp_path, capsys):
    legacy = build_legacy_run(tmp_path)
    name = render._safe_name("历史片段")
    (legacy / "segments" / (name + ".cleaned.wav")).write_bytes(b"malformed input WAV")
    output = tmp_path / "imported"
    code, payload = invoke(capsys, ["speech", "import-run", str(legacy), "--output", str(output)])
    assert code == 5 and payload["status"] == "failed", payload
    assert payload["run_dir"] == str(output.resolve())
    assert read_json(payload["report_path"])["status"] == "failed"
    assert not (output / "final" / ".complete").exists()


@pytest.mark.parametrize("command", ["plan", "import-run"])
def test_export_commands_do_not_overwrite_outputs(tmp_path, capsys, command):
    legacy = build_legacy_run(tmp_path)
    target = tmp_path / ("keep.json" if command == "plan" else "keep-dir")
    if command == "plan":
        target.write_text("keep", encoding="utf-8")
    else:
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
    code, payload = invoke(capsys, ["speech", command, str(legacy), "--output", str(target)])
    assert code == 2 and payload["status"] == "error"
    assert (target if command == "plan" else target / "keep.txt").read_text() == "keep"


def test_module_cli_emits_utf8_json_and_runs_assembly_without_models(tmp_path):
    request, _ = write_audio_request(tmp_path)
    source = run_alignment(request, tmp_path / "source", FakeExternalAligner())
    plan = create_assembly_plan(source.run_dir, tmp_path / "plan.json")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "regrain", "speech", "assemble", str(plan), "--output", str(tmp_path / "run")],
        env=env, capture_output=True, check=False,
    )
    assert completed.returncode == 0 and completed.stderr == b""
    assert "真人旁白".encode("utf-8") in completed.stdout
    assert b"\\u771f" not in completed.stdout
    payload = json.loads(completed.stdout.decode("utf-8"))
    assert payload["status"] == "complete" and Path(payload["wav_path"]).is_file()
