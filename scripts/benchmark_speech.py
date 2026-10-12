"""Reproducible CPU production benchmarks, with no model invocation.

Run fixture setup separately, then each timed operation in a fresh child:
    python scripts/benchmark_speech.py --source-root . --work-dir .tmp-research/perf

The source root may be an archived checkout. Timed trials have no I/O hooks;
one separate instrumented trial counts logical bytes crossing Python file APIs.
These counts describe application traffic, not physical disk or cache traffic.
shutil.copyfile traffic is counted once by file size, including OS fast copies.
Peak RSS includes interpreter/import memory but excludes fixture generation and
post-operation parity hashing. All WAVs are deterministic mono PCM16 at 24 kHz.
"""
from __future__ import annotations

import argparse
import builtins
import ctypes
from datetime import datetime, timezone
import hashlib
import io
import inspect
import json
import os
import platform
import shutil
import statistics
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

RATE = 24000
CASES = {"short90": (15, 6), "podcast1200": (24, 50)}
OPERATIONS = ("assembly", "revision", "external_preflight", "unit_load", "unit_snapshot")


def peak_rss() -> int:
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_working_set", "working_set", "quota_peak_paged", "quota_paged",
                    "quota_peak_nonpaged", "quota_nonpaged", "pagefile", "peak_pagefile")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        if not ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo(
            ctypes.c_void_p(kernel.GetCurrentProcess()), ctypes.byref(counters), counters.cb
        ):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return counters.peak_working_set
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def memory_available() -> dict:
    if os.name != "nt":
        return {}
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in (
                "total_physical", "available_physical", "total_pagefile", "available_pagefile",
                "total_virtual", "available_virtual", "extended_virtual")]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}
    return {"total_physical_bytes": status.total_physical,
            "available_physical_bytes": status.available_physical}


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_pcm(path: Path, frames: int, *, seed: int) -> None:
    # A bounded 1-second chunk contains varied non-small ints, making Python
    # list amplification representative without creating huge fixture lists.
    import array
    samples = array.array("h", (((index * 97 + seed * 17) % 20001) - 10000
                                for index in range(RATE)))
    if sys.byteorder != "little":
        samples.byteswap()
    block = samples.tobytes()
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, RATE, frames, "NONE", "not compressed"))
        while frames:
            count = min(frames, RATE)
            stream.writeframesraw(block[:count * 2])
            frames -= count


def setup(root: Path, case: str) -> dict:
    from regrain.artifacts import create_unit
    count, seconds = CASES[case]
    root.mkdir(parents=True, exist_ok=False)
    records = []
    for index in range(count):
        audio = root / f"source-{index:04d}.wav"
        alignment = root / f"source-{index:04d}.json"
        write_pcm(audio, RATE * seconds, seed=index + 1)
        if seconds <= 6:
            text = f"准确旁白第{index + 1}段。"
            tokens = [{"text": text.rstrip("。"), "start": 0.123456789,
                       "end": seconds - 0.123456789}]
        else:
            # Realistic short utterances avoid requesting a >6-second caption
            # from a single token (whose characters share exact token times).
            phrases = [f"准确旁白第{index + 1}段之{part + 1}句" for part in range(seconds // 5)]
            text = "。".join(phrases) + "。"
            tokens = [{"text": phrase, "start": part * 5 + 0.123456789,
                       "end": (part + 1) * 5 - 0.123456789}
                      for part, phrase in enumerate(phrases)]
        dump(alignment, tokens)
        unit = create_unit(root / "assets" / f"{index + 1:04d}", audio_path=audio,
                           alignment_path=alignment, text=text, language="Chinese",
                           provenance={"workflow": "cpu_benchmark", "unit_index": index})
        records.append({"id": f"unit-{index + 1}",
                        "artifact": unit.manifest_path.relative_to(root).as_posix(),
                        "sha256": unit.manifest_sha256, "pause_after_ms": 137})
        del unit  # Setup never retains all decoded Unit samples.
    dump(root / "assembly.json", {"schema_version": 1, "units": records})
    full_audio = root / "recording.wav"
    write_pcm(full_audio, RATE * count * seconds, seed=31)
    windows = []
    for index, start in enumerate(range(0, count * seconds, 300)):
        windows.append({"id": f"window-{index + 1}", "audio": "recording.wav",
                        "text": f"准确录音第{index + 1}段。", "start_frame": start * RATE,
                        "end_frame": min(start + 300, count * seconds) * RATE})
    dump(root / "audio.json", {"schema_version": 1, "language": "Chinese", "segments": windows})
    return {"case": case, "unit_count": count, "seconds_per_unit": seconds,
            "source_seconds": count * seconds, "sample_rate": RATE,
            "source_pcm_bytes": RATE * count * seconds * 2,
            "fixture_file_bytes": sum(path.stat().st_size for path in root.rglob("*") if path.is_file())}


class FileTraffic:
    """Count application file bytes; a copy adds one read and one write."""
    def __init__(self):
        self.counts = {"read_bytes": 0, "written_bytes": 0, "copy_bytes": 0,
                       "copy_operations": 0, "open_operations": 0}
        self.suspended = False

    def __enter__(self):
        self.old_open, self.old_io_open = builtins.open, io.open
        self.old_copy = shutil.copyfile
        tracker = self

        class Tracked:
            def __init__(self, handle):
                self.handle = handle

            def __getattr__(self, key):
                return getattr(self.handle, key)

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def __iter__(self):
                return self

            def __next__(self):
                value = next(self.handle)
                self._read(value)
                return value

            def _size(self, data):
                if isinstance(data, str):
                    return len(data.encode(self.handle.encoding or "utf-8"))
                return len(data)

            def _read(self, data):
                if not tracker.suspended:
                    tracker.counts["read_bytes"] += self._size(data)

            def read(self, *args):
                value = self.handle.read(*args)
                self._read(value)
                return value

            def readline(self, *args):
                value = self.handle.readline(*args)
                self._read(value)
                return value

            def readinto(self, *args):
                value = self.handle.readinto(*args)
                if not tracker.suspended:
                    tracker.counts["read_bytes"] += value or 0
                return value

            def write(self, data):
                result = self.handle.write(data)
                if not tracker.suspended:
                    tracker.counts["written_bytes"] += self._size(data[:result])
                return result

        def wrap(original):
            def opening(*args, **kwargs):
                handle = original(*args, **kwargs)
                if not tracker.suspended:
                    tracker.counts["open_operations"] += 1
                return Tracked(handle)
            return opening

        def copying(source, destination, *args, **kwargs):
            size = os.stat(source).st_size
            tracker.suspended = True
            try:
                result = tracker.old_copy(source, destination, *args, **kwargs)
            finally:
                tracker.suspended = False
            tracker.counts["copy_bytes"] += size
            tracker.counts["copy_operations"] += 1
            tracker.counts["read_bytes"] += size
            tracker.counts["written_bytes"] += size
            return result

        builtins.open, io.open = wrap(self.old_open), wrap(self.old_io_open)
        shutil.copyfile = copying
        return self

    def __exit__(self, *args):
        builtins.open, io.open, shutil.copyfile = self.old_open, self.old_io_open, self.old_copy


def final_parity(root: Path) -> dict:
    final = root / "final"
    pcm_hash = hashlib.sha256()
    with wave.open(str(final / "final.wav"), "rb") as stream:
        rate, frames = stream.getframerate(), stream.getnframes()
        while block := stream.readframes(RATE):
            pcm_hash.update(block)
    return {"pcm_sha256": pcm_hash.hexdigest(), "sample_rate": rate, "frames": frames,
            "wav_sha256": digest(final / "final.wav"), "srt_sha256": digest(final / "final.srt"),
            "timeline_sha256": digest(final / "timeline.json"),
            "marker": (final / ".complete").read_text(encoding="utf-8")}


def operation(root: Path, name: str, destination: Path, instrument: bool) -> dict:
    from regrain.artifacts import load_unit, snapshot_unit
    from regrain.external_audio import load_audio_request
    from regrain.production import assemble_speech
    # The public helper keeps eager samples for API compatibility. Benchmark
    # the Controller's preflight path: original versions have only eager mode;
    # optimized versions explicitly request their internal metadata mode.
    metadata_preflight = "_metadata_only" in inspect.signature(load_audio_request).parameters
    preflight_kwargs = {"_metadata_only": True} if metadata_preflight else {}
    traffic = FileTraffic()
    start = time.perf_counter()
    if instrument:
        traffic.__enter__()
    try:
        if name in ("assembly", "revision"):
            plan = root / ("assembly.json" if name == "assembly" else "revision.json")
            value = assemble_speech(plan, destination)
            assert value.status == "complete"
        elif name == "external_preflight":
            value = load_audio_request(root / "audio.json", **preflight_kwargs)
        elif name == "unit_load":
            value = load_unit(root / "assets" / "0001" / "unit.json")
        elif name == "unit_snapshot":
            unit = load_unit(root / "assets" / "0001" / "unit.json")
            value = snapshot_unit(unit, destination)
        else:
            raise ValueError(name)
    finally:
        elapsed = time.perf_counter() - start
        peak = peak_rss()
        if instrument:
            traffic.__exit__()
    # Keep operation's return values alive until peak sampling. Do not include
    # semantic output scanning or revision-plan generation in operation timing.
    result = {"operation": name, "wall_seconds": elapsed, "peak_rss_bytes": peak,
              "instrumented": instrument}
    if instrument:
        result["logical_io"] = traffic.counts
    if name in ("assembly", "revision"):
        result["parity"] = final_parity(destination)
    if name == "external_preflight":
        result["preflight_mode"] = "controller_metadata" if metadata_preflight else "original_controller_eager"
        result["windows"] = len(value[1])
        result["window_frames"] = [window.end_frame - window.start_frame for window in value[1]]
        # The presence of an eager `samples` list is an API implementation
        # detail. All source pins, transcripts and integer windows must agree.
        records = [{"id": window.id, "text": window.text, "source_sha256": window.source_sha256,
                    "sample_rate": window.sample_rate, "start_frame": window.start_frame,
                    "end_frame": window.end_frame, "pause_after_ms": window.pause_after_ms}
                   for window in value[1]]
        result["window_metadata_sha256"] = hashlib.sha256(
            json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        window_digests = []
        for window in value[1]:
            pinned = getattr(window, "pcm_sha256", None)
            if pinned is None:
                # Original Controller's eager samples are the bytes it would
                # snapshot. Hash bounded chunks after time/peak sampling.
                pcm_hash = hashlib.sha256()
                for offset in range(0, len(window.samples), 65_536):
                    block = window.samples[offset:offset + 65_536]
                    pcm_hash.update(struct.pack(f"<{len(block)}h", *block))
                pinned = pcm_hash.hexdigest()
            window_digests.append(pinned)
        result["window_pcm_sha256"] = window_digests
    if name.startswith("unit_"):
        result["unit_identity"] = {"content_id": value.content_id,
                                   "manifest_sha256": value.manifest_sha256, "frames": value.frames}
    return result


def child(args) -> None:
    sys.path.insert(0, str(args.source_root.resolve() / "src"))
    if args.child == "setup":
        result = setup(args.fixture, args.case)
    else:
        result = operation(args.fixture, args.child, args.destination, args.instrument)
    dump(args.result, result)


def run_child(args, name: str, case: str, fixture: Path, destination: Path, result: Path,
              instrument: bool = False) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--child", name,
               "--source-root", str(args.source_root.resolve()), "--case", case,
               "--fixture", str(fixture), "--destination", str(destination), "--result", str(result)]
    if instrument:
        command.append("--instrument")
    subprocess.run(command, check=True)
    return json.loads(result.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--work-dir", type=Path, default=Path(".tmp-research/speech-benchmark"))
    parser.add_argument("--fixture-root", type=Path, help="Reuse fixture assets prepared by an earlier run")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--operations", nargs="+", choices=OPERATIONS, default=list(OPERATIONS))
    parser.add_argument("--child", choices=("setup",) + OPERATIONS)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--instrument", action="store_true")
    args = parser.parse_args()
    if not (args.source_root / "src" / "regrain" / "__init__.py").is_file():
        parser.error("--source-root must contain src/regrain/__init__.py")
    if args.child:
        child(args)
        return
    if args.trials < 1:
        parser.error("--trials must be positive")
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=False)
    fixture_root = args.fixture_root.resolve() if args.fixture_root else work / "fixtures"
    metadata = {"platform": platform.platform(), "python": sys.version,
                "processor": platform.processor(), "cpu_count": os.cpu_count(),
                "source_root": str(args.source_root.resolve()), "memory": memory_available(),
                "trials": args.trials, "io_semantics": "logical application bytes; copyfile counts one read/write; no physical disk measurement",
                "timing_semantics": "fresh child wall time after imports; fixture setup and parity scans excluded; warm OS cache uncontrolled",
                "memory_semantics": "process lifetime peak RSS before parity scan; interpreter/imports included; fixture setup excluded"}
    metadata["started_utc"] = datetime.now(timezone.utc).isoformat()
    tree_hash = hashlib.sha256()
    for source_path in sorted((args.source_root.resolve() / "src" / "regrain").rglob("*.py")):
        tree_hash.update(source_path.relative_to(args.source_root.resolve()).as_posix().encode("utf-8"))
        tree_hash.update(source_path.read_bytes())
    metadata["source_tree_sha256"] = tree_hash.hexdigest()
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            metadata["processor_name"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    try:
        metadata["workspace_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        pass
    cases = []
    for case in args.cases:
        fixture = fixture_root / case
        if not fixture.exists():
            fixture_data = run_child(args, "setup", case, fixture, work, work / f"{case}-setup.json")
        else:
            count, seconds = CASES[case]
            fixture_data = {"case": case, "unit_count": count, "seconds_per_unit": seconds,
                            "source_seconds": count * seconds, "sample_rate": RATE,
                            "source_pcm_bytes": RATE * count * seconds * 2,
                            "fixture_file_bytes": sum(p.stat().st_size for p in fixture.rglob("*") if p.is_file())}
        case_data = {"fixture": fixture_data, "operations": {}}
        for name in args.operations:
            if name == "revision":
                # Reference a completed run's independently owned snapshots.
                first_run = work / f"{case}-assembly-0"
                if not (first_run / "final" / ".complete").exists():
                    run_child(args, "assembly", case, fixture, first_run, work / f"{case}-revision-setup.json")
                source_plan = json.loads((fixture / "assembly.json").read_text(encoding="utf-8"))
                for index, record in enumerate(source_plan["units"], 1):
                    manifest = first_run / "units" / f"{index:04d}" / "unit.json"
                    record["artifact"] = Path(os.path.relpath(manifest, fixture)).as_posix()
                    record["sha256"] = digest(manifest)
                    record["pause_after_ms"] = 251
                dump(fixture / "revision.json", source_plan)
            trials = []
            for trial in range(args.trials):
                output = work / f"{case}-{name}-{trial}"
                result_path = work / f"{case}-{name}-{trial}.json"
                result = run_child(args, name, case, fixture, output, result_path)
                trials.append(result)
                print(f"{case} {name} trial {trial + 1}: {result['wall_seconds']:.3f}s "
                      f"{result['peak_rss_bytes'] / 2**20:.1f} MiB", flush=True)
            instrumented = run_child(args, name, case, fixture, work / f"{case}-{name}-io",
                                     work / f"{case}-{name}-io.json", instrument=True)
            if name in ("assembly", "revision"):
                assert all(item["parity"] == trials[0]["parity"] for item in trials + [instrumented]), \
                    f"non-deterministic Final bytes in {case}/{name}"
            case_data["operations"][name] = {"median_wall_seconds": statistics.median(item["wall_seconds"] for item in trials),
                "median_peak_rss_bytes": statistics.median(item["peak_rss_bytes"] for item in trials),
                "trials": trials, "instrumented": instrumented}
            dump(work / "results.json", {"metadata": metadata, "cases": cases + [case_data]})
        cases.append(case_data)
    dump(work / "results.json", {"metadata": metadata, "cases": cases})


if __name__ == "__main__":
    main()
