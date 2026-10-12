"""Thin, JSON-output CLI for the existing production speech entry points."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .render import (
    STATUS_COMPLETE,
    RenderError,
    load_and_validate_script,
    render_speech,
)
from .artifacts import ArtifactError
from .production import assemble_speech, create_assembly_plan, import_render_run
from .external_audio import align_speech, load_audio_request


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="regrain",
        description="Render, align and revise speech; results are UTF-8 JSON.",
        allow_abbrev=False,
    )
    from . import __version__

    parser.add_argument("--version", action="version", version=f"regrain {__version__}")
    groups = parser.add_subparsers(dest="group", required=True)
    speech = groups.add_parser("speech", allow_abbrev=False, help="speech tools")
    commands = speech.add_subparsers(dest="command", required=True)
    for command in ("validate", "render"):
        sub = commands.add_parser(
            command, allow_abbrev=False,
            help=("static script validation (no models)" if command == "validate"
                  else "render using separate TTS and alignment interpreters"),
        )
        sub.add_argument("script", help="caller-authored UTF-8 speech JSON")
        sub.add_argument(
            "--max-segment-chars",
            type=int,
            default=200,
            help="maximum Unicode code points per performance unit (default: 200)",
        )
        if command == "render":
            sub.add_argument("--output", required=True, help="new render run directory")
            for name in ("tts-python", "alignment-python", "tts-model", "alignment-model"):
                sub.add_argument(
                    "--" + name,
                    help="override REGRAIN_" + name.upper().replace("-", "_"),
                )
            sub.add_argument(
                "--device", default="cuda:0", help="model device (default: cuda:0)"
            )
    for command, argument, help_text in (
        ("assemble", "plan", "assemble pinned speech Units without model calls"),
        ("align", "request", "align supplied audio and exact transcripts"),
        ("plan", "run", "export an editable, pinned assembly plan"),
        ("import-run", "run", "snapshot completed legacy render Units"),
    ):
        sub = commands.add_parser(command, allow_abbrev=False, help=help_text)
        sub.add_argument(argument)
        sub.add_argument("--output", required=True)
        if command == "align":
            sub.add_argument("--alignment-python", help="override REGRAIN_ALIGNMENT_PYTHON")
            sub.add_argument("--alignment-model", help="override REGRAIN_ALIGNMENT_MODEL")
            sub.add_argument("--device", default="cuda:0")
    # Reuse the existing deterministic caption compiler's argument contract.
    from .captions_cli import build_parser as captions_parser
    groups.add_parser("captions", parents=[captions_parser()], add_help=False, allow_abbrev=False)
    return parser


def _emit(command: str | None, status: str, **fields: object) -> None:
    payload = {"schema_version": 1, "command": command, "status": status, **fields}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(text)
    else:
        stream.write(text.encode("utf-8", errors="backslashreplace"))
        stream.flush()


def _path(path: str | Path | None) -> str | None:
    return str(Path(path).resolve()) if path is not None else None


def _production_command(args: argparse.Namespace) -> int:
    command = args.command
    try:
        if command == "align":
            # Validate caller audio/transcript before requesting any runtime configuration.
            try:
                prepared = load_audio_request(args.request, _metadata_only=True)
            except (ValueError, OSError) as exc:
                _emit(command, "validation_failed", error=str(exc))
                return 3
            runtime = {}
            for name in ("alignment_python", "alignment_model"):
                value = getattr(args, name)
                if value is None:
                    value = os.environ.get("REGRAIN_" + name.upper())
                if not value or not value.strip():
                    _emit(command, "error", error="missing runtime configuration: REGRAIN_" + name.upper())
                    return 2
                runtime[name] = value
            result = align_speech(args.request, args.output, device=args.device, _prepared=prepared, **runtime)
        elif command == "assemble":
            result = assemble_speech(args.plan, args.output)
        elif command == "plan":
            path = create_assembly_plan(args.run, args.output)
            _emit(command, "complete", plan_path=_path(path))
            return 0
        else:
            path = import_render_run(args.run, args.output)
            _emit(command, "complete", run_dir=_path(path), report_path=_path(path / "report.json"))
            return 0
    except RenderError as exc:
        report = exc.run_dir / "report.json" if exc.run_dir is not None else None
        validation = exc.run_dir is None and isinstance(exc.__cause__, (ArtifactError, OSError, ValueError))
        _emit(command, "failed" if exc.run_dir is not None else ("validation_failed" if validation else "error"), error=str(exc),
              run_dir=_path(exc.run_dir), report_path=_path(report) if report is not None and report.is_file() else None,
              wav_path=None, srt_path=None, timeline_path=None)
        return 5 if exc.run_dir is not None else (3 if validation else 2)
    except (ArtifactError, OSError, ValueError) as exc:
        _emit(command, "error", error=str(exc), wav_path=None, srt_path=None, timeline_path=None)
        return 2
    _emit(command, result.status, run_dir=_path(result.run_dir), report_path=_path(result.report_path),
          wav_path=_path(result.wav_path), srt_path=_path(result.srt_path), timeline_path=_path(result.timeline_path),
          segments=[asdict(seg) for seg in result.segments])
    return 0 if result.status == STATUS_COMPLETE else 4


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "captions":
        from .captions_cli import main as captions_main
        return captions_main(argv[1:])
    try:
        args = build_parser().parse_args(argv)
    except _UsageError as exc:
        _emit(None, "error", error=str(exc))
        return 2

    if args.command in ("assemble", "align", "plan", "import-run"):
        return _production_command(args)

    # Static preflight always precedes runtime configuration and rendering.
    # The renderer retains its own preflight; its Python API stays untouched.
    try:
        script = load_and_validate_script(
            args.script, max_segment_chars=args.max_segment_chars
        )
    except (RenderError, OSError, UnicodeError) as exc:
        _emit(
            args.command, "validation_failed",
            script_path=_path(args.script), error=str(exc),
        )
        return 3

    if args.command == "validate":
        _emit(
            "validate", "valid", script_path=_path(args.script),
            plan={
                "language": script.language,
                **(
                    {"speaker": script.speaker}
                    if script.clone_asset is None
                    else {"voice": {"type": "clone", "asset": script.clone_asset}}
                ),
                "max_segment_chars": script.max_segment_chars,
                "segments": [
                    {
                        "order": order,
                        "id": segment.id,
                        "text": segment.text,
                        "text_length": len(segment.text),
                        "pause_after_ms": segment.pause_after_ms,
                        "effective_instruct": (
                            script.instruct if segment.instruct is None else segment.instruct
                        ),
                        "instruct_source": (
                            "top_level" if segment.instruct is None else "segment"
                        ),
                    }
                    for order, segment in enumerate(script.segments, start=1)
                ],
            },
        )
        return 0

    runtime = {}
    missing = []
    for name in ("tts_python", "alignment_python", "tts_model", "alignment_model"):
        env_name = "REGRAIN_" + name.upper()
        value = getattr(args, name)
        if value is None:
            value = os.environ.get(env_name)
        if not value or not value.strip():
            missing.append("--" + name.replace("_", "-") + " / " + env_name)
        runtime[name] = value
    if missing:
        _emit(
            "render", "error",
            error="missing runtime configuration: " + ", ".join(missing),
        )
        return 2

    try:
        result = render_speech(
            args.script, args.output, max_segment_chars=args.max_segment_chars,
            device=args.device, **runtime,
        )
    except RenderError as exc:
        run_dir = exc.run_dir
        report = run_dir / "report.json" if run_dir is not None else None
        _emit(
            "render", "failed" if run_dir is not None else "error",
            error=str(exc), run_dir=_path(run_dir),
            report_path=_path(report) if report is not None and report.is_file() else None,
            wav_path=None, srt_path=None, timeline_path=None,
        )
        return 5 if run_dir is not None else 2
    except (OSError, UnicodeError) as exc:
        _emit("render", "error", error=str(exc))
        return 2

    _emit(
        "render", result.status,
        run_dir=_path(result.run_dir), report_path=_path(result.report_path),
        wav_path=_path(result.wav_path), srt_path=_path(result.srt_path),
        timeline_path=_path(result.timeline_path),
        segments=[asdict(segment) for segment in result.segments],
    )
    return 0 if result.status == STATUS_COMPLETE else 4


if __name__ == "__main__":
    raise SystemExit(main())
