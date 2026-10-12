"""Shared deterministic CPU model-stage fakes; these never validate real models."""

from __future__ import annotations

import json
import math
import unicodedata
from functools import lru_cache
from pathlib import Path

from regrain.artifacts import write_json
from regrain.captions import has_alignable_content
from regrain.external_audio import align_speech
from regrain.postprocess import read_wav, write_wav

SAMPLE_RATE = 24_000

class FakeTTSEngine:
    """Models the TTS subprocess: one construction == one model load.

    ``loads`` counts constructions so tests can assert the orchestrator builds
    the engine exactly once (one model load per run). ``durations`` maps segment
    id -> synthesized WAV length in seconds; ``fail_ids`` report a synthesis
    failure, ``wav_error_ids`` write an invalid WAV that the parent must reject.
    """

    loads = 0

    def __init__(
        self,
        durations: dict[str, float] | None = None,
        fail_ids: set[str] | None = None,
        wav_error_ids: set[str] | None = None,
        rates: dict[str, int] | None = None,
    ) -> None:
        FakeTTSEngine.loads += 1
        self.durations = durations or {}
        self.fail_ids = set(fail_ids or [])
        self.wav_error_ids = set(wav_error_ids or [])
        self.rates = rates or {}

    def __call__(self, task: dict) -> dict:
        segments_dir = Path(task["run_dir"]) / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for seg in task["segments"]:
            rate = self.rates.get(seg["id"], SAMPLE_RATE)
            wav = segments_dir / (seg["safe_name"] + ".wav")
            frames = int(self.durations.get(seg["id"], 1.0) * rate)
            if seg["id"] in self.wav_error_ids:
                # A mono-but-empty WAV: read_wav rejects it as a run-level fault.
                write_wav(wav, [], rate)
            else:
                write_wav(wav, _waveform(frames), rate)
            if seg["id"] in self.fail_ids:
                results.append(
                    {
                        "segment_id": seg["id"],
                        "status": "synthesis_failed",
                        "reason": "simulated model failure",
                    }
                )
            else:
                results.append({"segment_id": seg["id"], "status": "ok"})
        return {"runtime_error": None, "segments": results}



class FakeAlignerEngine:
    """Models the Alignment subprocess: one construction == one model load.

    ``fail_ids`` report an isolatable deterministic ``alignment_failed`` while
    other independent segments still succeed; ``runtime`` returns a
    model-runtime error that stops the whole run.
    """

    loads = 0

    def __init__(
        self,
        fail_ids: set[str] | None = None,
        *,
        runtime: bool = False,
    ) -> None:
        FakeAlignerEngine.loads += 1
        self.fail_ids = set(fail_ids or [])
        self.runtime = runtime

    def __call__(self, task: dict) -> dict:
        if self.runtime:
            return {"runtime_error": "simulated CUDA/model failure", "segments": []}

        segments_dir = Path(task["run_dir"]) / "segments"
        results = []
        for seg in task["segments"]:
            raw = segments_dir / (seg["safe_name"] + ".alignment.raw.json")
            _, rate, frames = read_wav(segments_dir / (seg["safe_name"] + ".wav"))
            duration = frames / rate
            if seg["id"] in self.fail_ids:
                _write_bad_alignment(raw, seg["text"], duration)
                results.append(
                    {
                        "segment_id": seg["id"],
                        "status": "alignment_failed",
                        "reason": "alignment text does not match",
                    }
                )
            else:
                _write_alignment(raw, seg["text"], duration)
                results.append({"segment_id": seg["id"], "status": "ok"})
        return {"runtime_error": None, "segments": results}



@lru_cache(maxsize=32)
def _waveform(frames: int) -> tuple[int, ...]:
    """A finite, non-silent, mono float waveform -> int16 PCM (never non-finite)."""

    out: list[int] = []
    for index in range(frames):
        decay = max(0.05, 1.0 - index / max(1, frames))
        f = 0.3 * math.sin(index / 80.0) * decay
        q = int(math.floor(f * 32_768 + 0.5))
        out.append(max(-32_768, min(32_767, q)))
    return tuple(out)



def _write_alignment(path: Path, text: str, duration: float) -> None:
    """One aligned token per spoken character, strictly increasing, inside the WAV."""

    skippable = frozenset("PZ")

    def _spoken(s: str) -> list[str]:
        return [c for c in s if not (c.isspace() or unicodedata.category(c)[0] in skippable)]

    spoken = _spoken(text)
    if not spoken:
        spoken = ["x"]
    records = []
    time = 0.2
    step = max(0.01, (duration - 0.3) / len(spoken))
    for i, char in enumerate(spoken):
        start = time
        end = min(time + step, duration - 0.05)
        records.append({"text": char, "start": start, "end": end})
        time = end
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



def _write_bad_alignment(path: Path, text: str, duration: float) -> None:
    """A malformed alignment: a single token whose text does not match the script."""

    path.write_text(
        json.dumps([{"text": "无关文本", "start": 0.2, "end": min(0.5, duration - 0.05)}])
        + "\n",
        encoding="utf-8",
    )



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
