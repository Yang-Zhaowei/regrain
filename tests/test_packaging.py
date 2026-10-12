"""Opt-in, CPU-only acceptance of an already-built wheel, without networking.

Set REGRAIN_TEST_WHEEL to its absolute path. The tests install it into a clean
temporary Controller environment and never import production code from checkout.
The model stages below are lightweight subprocess fakes, not GPU validation.
"""

from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import venv
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path

import pytest


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    for key in list(env):
        if key.startswith(("UV_", "PIP_")):
            env.pop(key)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(command: list[str | Path], cwd: Path) -> str:
    completed = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def _python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@pytest.fixture(scope="module")
def wheel() -> Path:
    configured = os.environ.get("REGRAIN_TEST_WHEEL")
    if not configured:
        pytest.skip("set REGRAIN_TEST_WHEEL to an existing wheel for installed acceptance")
    path = Path(configured).resolve()
    assert path.is_file() and path.suffix == ".whl", f"not a wheel file: {path}"
    return path


@dataclass(frozen=True)
class Controller:
    python: Path
    package: Path
    cwd: Path

    def cli(self, name: str) -> Path:
        return self.python.parent / (name + ".exe" if os.name == "nt" else name)


@pytest.fixture(scope="module")
def controller(wheel: Path, tmp_path_factory: pytest.TempPathFactory) -> Controller:
    root = tmp_path_factory.mktemp("regrain-installed")
    environment = root / "controller"
    installer = shutil.which("uv")
    venv.EnvBuilder(with_pip=installer is None).create(environment)
    python = _python(environment)
    if installer:
        # Use an available installer without bootstrapping pip in every clean
        # environment. No network, dependency resolution or shared cache.
        _run([installer, "pip", "install", "--python", python, "--no-index",
              "--no-deps", "--no-config", "--no-cache", "--offline", wheel], root)
    else:
        _run([python, "-m", "pip", "--disable-pip-version-check", "install",
              "--no-index", "--no-deps", wheel], root)
    package = Path(_run(
        [python, "-c", "import regrain; print(regrain.__file__)"], root,
    ).strip()).parent
    assert package.is_relative_to(environment)
    return Controller(python=python, package=package, cwd=root)


def test_wheel_metadata_and_only_production_contents(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_path, = [name for name in names if name.endswith(".dist-info/METADATA")]
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        assert metadata["Name"] == "regrain"
        assert metadata["Version"] == "0.2.0"
        assert metadata["Requires-Python"] == ">=3.10"
        # Development extras are allowed; the Controller has no required dependencies.
        assert all("extra ==" in requirement.partition(";")[2]
                   for requirement in metadata.get_all("Requires-Dist", []))
        entrypoints_path, = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        entrypoints = configparser.ConfigParser()
        entrypoints.read_string(archive.read(entrypoints_path).decode("utf-8"))
        assert dict(entrypoints["console_scripts"]) == {
            "regrain": "regrain.cli:main",
        }
        assert not any(name.startswith("media_pipeline/") for name in names)
        assert not any({"tests", "experiments", "fixtures"}.intersection(Path(name).parts)
                       for name in names)
        for relative in ("SKILL.md", "examples/custom-voice.json", "examples/clone.json"):
            assert "regrain/skills/regrain-speech/" + relative in names


def test_installed_cli_naming_and_skill_examples(controller: Controller) -> None:
    assert not controller.cli("media-pipeline").exists()
    for option in ("--help", "--version"):
        primary = _run([controller.cli("regrain"), option], controller.cwd)
        assert primary == _run([controller.python, "-m", "regrain", option], controller.cwd)
        assert "regrain" in primary
        if option == "--version":
            assert primary.strip() == "regrain 0.2.0"
        else:
            assert "speech" in primary
    inspection = json.loads(_run([controller.python, "-c", """
import importlib.metadata
import importlib.util
import json
import regrain
assert regrain.__version__ == importlib.metadata.version('regrain') == '0.2.0'
assert importlib.util.find_spec('media_pipeline') is None
print(json.dumps({name: importlib.util.find_spec(name) is not None
                  for name in ('torch', 'qwen_tts', 'qwen_asr', 'soundfile')}))
"""], controller.cwd))
    assert not any(inspection.values())
    examples = controller.package / "skills" / "regrain-speech" / "examples"
    for filename, mode in (("custom-voice.json", "custom"), ("clone.json", "clone")):
        example = examples / filename
        payload = json.loads(example.read_text(encoding="utf-8"))
        if mode == "clone":
            assert not (example.parent / payload["voice"]["asset"]).exists()
        result = json.loads(_run(
            [controller.cli("regrain"), "speech", "validate", example], controller.cwd,
        ))
        assert result["status"] == "valid"
        assert result["plan"]["segments"]
        assert ("voice" in result["plan"]) == (mode == "clone")
    module_help = _run(
        [controller.python, "-m", "regrain", "--help"], controller.cwd,
    )
    assert "speech" in module_help


_WORKER_COMMON = r"""
import json
import sys
from pathlib import Path
import regrain
import _regrain_packaging_probe as probe

spec = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
expected_package, task = spec['args']
assert Path(regrain.__file__).resolve().parent == Path(expected_package).resolve()
assert regrain.__version__ == '0.2.0'
assert Path(probe.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
segments_dir = Path(task['run_dir']) / 'segments'
segments_dir.mkdir(parents=True, exist_ok=True)
results = []
"""

_TTS_WORKER = _WORKER_COMMON + r"""
assert probe.IDENTITY == 'tts'
import math
from regrain.postprocess import write_wav
samples = [int(7000 * math.sin(index / 80)) for index in range(24000)]
for segment in task['segments']:
    write_wav(segments_dir / (segment['safe_name'] + '.wav'), samples, 24000)
    results.append({'segment_id': segment['id'], 'status': 'ok'})
Path(spec['manifest']).write_text(json.dumps({'runtime_error': None, 'segments': results}),
                                  encoding='utf-8')
"""

_ALIGNMENT_WORKER = _WORKER_COMMON + r"""
assert probe.IDENTITY == 'alignment'
import unicodedata
for segment in task['segments']:
    spoken = [char for char in segment['text']
              if not char.isspace() and unicodedata.category(char)[0] not in 'PZ']
    step = 0.7 / len(spoken)
    records = [{'text': char, 'start': 0.2 + index * step,
                'end': 0.2 + (index + 1) * step} for index, char in enumerate(spoken)]
    path = segments_dir / (segment['safe_name'] + '.alignment.raw.json')
    path.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    results.append({'segment_id': segment['id'], 'status': 'ok'})
Path(spec['manifest']).write_text(json.dumps({'runtime_error': None, 'segments': results}),
                                  encoding='utf-8')
"""

_CONTROLLER_RUN = r"""
import json
import sys
from pathlib import Path
import regrain
from regrain.render import render_speech, _run_model_stage
from regrain import align_speech, assemble_speech, create_assembly_plan
from regrain.postprocess import read_wav
from regrain.render import _safe_name

config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
assert Path(regrain.__file__).resolve().parent == Path(config['package']).resolve()
def task(stage):
    return lambda request: _run_model_stage(
        interpreter=config[stage + '_python'], stage_script=config[stage + '_script'],
        args=(config['package'], request),
    )
result = render_speech(
    config['speech'], config['output'], tts_python=config['tts_python'],
    alignment_python=config['alignment_python'], tts_model='cpu-fake',
    alignment_model='cpu-fake', max_segment_chars=200,
    _wav_task=task('tts'), _align_task=task('alignment'),
)
assert result.status == 'complete'
# Installed production APIs run without a checkout or heavy Controller deps.
root = Path(config['output']).parent
plan = root / 'assembly.json'
create_assembly_plan(result.run_dir, plan)
revised = assemble_speech(plan, root / 'revised')
assert revised.status == 'complete'
assert read_wav(revised.wav_path) == read_wav(result.wav_path)
first = json.loads(Path(config['speech']).read_text(encoding='utf-8'))['segments'][0]
recording = result.run_dir / 'segments' / (_safe_name(first['id']) + '.wav')
audio_request = root / 'alignment.json'
audio_request.write_text(json.dumps({'schema_version': 1, 'language': 'Chinese',
    'segments': [{'id': 'recorded', 'text': first['text'],
                  'audio': recording.relative_to(root).as_posix()}]}), encoding='utf-8')
aligned = align_speech(audio_request, root / 'aligned',
    alignment_python=config['alignment_python'], alignment_model='cpu-fake',
    _align_task=task('alignment'))
assert aligned.status == 'complete'
assert read_wav(aligned.wav_path) == read_wav(recording)
print(result.status)
"""


def test_installed_controller_pins_package_and_preserves_worker_dependencies(
    controller: Controller, tmp_path: Path,
) -> None:
    config = {"package": str(controller.package)}
    # A neighboring Controller dependency must not enter either worker's path.
    (controller.package.parent / "_regrain_packaging_probe.py").write_text(
        "IDENTITY = 'controller'\n", encoding="utf-8",
    )
    for stage in ("tts", "alignment"):
        environment = tmp_path / stage
        venv.EnvBuilder(with_pip=False).create(environment)
        python = _python(environment)
        site_packages = Path(json.loads(_run(
            [python, "-c", "import json, sysconfig; print(json.dumps(sysconfig.get_path('purelib')))"],
            tmp_path,
        )))
        (site_packages / "_regrain_packaging_probe.py").write_text(
            f"IDENTITY = {stage!r}\n", encoding="utf-8",
        )
        stale_package = site_packages / "regrain"
        stale_package.mkdir()
        (stale_package / "__init__.py").write_text(
            "raise AssertionError('stale worker package was imported')\n", encoding="utf-8",
        )
        config[stage + "_python"] = str(python)
    speech = tmp_path / "speech.json"
    speech.write_text(json.dumps({
        "language": "Chinese", "speaker": "Uncle_Fu", "segments": [
            {"id": "opening", "text": "欢迎收听。", "pause_after_ms": 125},
            {"id": "next", "text": "我们开始。"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "rendered"
    config.update(speech=str(speech), output=str(output),
                  tts_script=_TTS_WORKER, alignment_script=_ALIGNMENT_WORKER)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    driver = tmp_path / "render.py"
    driver.write_text(_CONTROLLER_RUN, encoding="utf-8")
    assert _run([controller.python, driver, config_path], tmp_path).strip() == "complete"
    for artifact in ("final.wav", "final.srt", "timeline.json", ".complete"):
        assert (output / "final" / artifact).is_file()
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert report["completed_stages"] == [
        "preflight", "tts", "alignment", "postprocess", "captions", "assemble",
    ]
    timeline = json.loads((output / "final" / "timeline.json").read_text(encoding="utf-8"))
    assert [segment["segment_id"] for segment in timeline["segments"]] == ["opening", "next"]
    assert timeline["total_frames"] > 0
    assert "欢迎收听" in (output / "final" / "final.srt").read_text(encoding="utf-8")
    exported = tmp_path / "cli-assembly.json"
    assert json.loads(_run([controller.cli("regrain"), "speech", "plan", output,
                           "--output", exported], tmp_path))["status"] == "complete"
    assembled = tmp_path / "cli-revised"
    assert json.loads(_run([controller.cli("regrain"), "speech", "assemble", exported,
                           "--output", assembled], tmp_path))["status"] == "complete"
