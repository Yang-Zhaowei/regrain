# regrain

Local-first speech production: turn approved text or recordings into WAV, SRT
and a frame timeline, then reuse Units to select takes, replace passages and edit
pauses without regenerating the whole program. The lightweight Controller runs
separately from the existing TTS and Aligner model environments.

## Start here

| What you have | What to do | Models needed |
| --- | --- | --- |
| Narration text and a preset or cloned voice | [Validate and render](#render-narration) | TTS + Aligner on Core |
| Human/third-party recording and exact transcript | [Align the recording](#align-a-recording) | Aligner on Core |
| A run with reusable Units | [Edit pauses, order or takes](#revise-without-model-calls) | None |
| Completed v0.1 run without Unit manifests | [Import once](#reuse-a-legacy-run), then revise | None |
| An Agent task | Use [regrain-speech](skills/regrain-speech/SKILL.md) | Depends on the operation |

Use the installed `regrain` CLI from your content-project directory. On Client,
install a selected local wheel whose SHA256 matches its manifest, using uv-managed
Python and `uv tool`:

```console
uv python install 3.12
uv tool install --managed-python --python 3.12 --no-index ./regrain-0.2.0-py3-none-any.whl
regrain --version
regrain --help
```

[Installation, PATH discovery and rollback](docs/deployment.md#client-installation-and-authoring)
cover the complete procedure. Core uses a separate versioned uv Controller and
its direct installed executable; configure its existing TTS/Aligner Workers via
[Core runtime settings](docs/deployment.md#core-runtime-configuration-and-daily-execution).
Assembly and legacy import also work on Client without those model settings.
If you do not have a selected wheel yet, use the
[development checkout](#status-release-and-development) for local validation and
CPU workflows; prefix CLI examples with `uv run` in that checkout.

Each output run directory and exported plan file must be new. Use a different
name for every revision. Regrain preserves input files rather than editing them
in place.

## Render narration

Copy the [CustomVoice example](skills/regrain-speech/examples/custom-voice.json)
or [clone example](skills/regrain-speech/examples/clone.json) into your project as
`speech.json`, then use your approved text. CustomVoice needs a checkpoint-supported
`speaker`; clone needs a trusted asset at the script-relative `voice.asset` path
and omitted/empty instructions. Select the matching CustomVoice or Base checkpoint
in Core configuration, not in the script.

```console
regrain speech validate speech.json
regrain speech render speech.json --output runs/tts-v1
```

`validate` is model-free static preflight; it does not check clone asset existence
or audible quality. The default budget is 200 Unicode code points per Performance
Unit, not a model limit. See [script and CLI rules](docs/dev/render-entry-point.md)
for instructions, budgets, runtime options and errors.

## Align a recording

Copy the [recording request](skills/regrain-speech/examples/recording.json) as
`alignment.json` beside your mono PCM16 `recording.wav`. Set `text` to the exact
spoken transcript; the CLI does not read a transcript filename or perform ASR.

```console
regrain speech align alignment.json --output runs/recorded-v1
```

Only the Aligner is needed. Default `preserve` mode keeps selected PCM and natural
pauses. `speech validate` accepts narration scripts only; `align` checks its own
request. [Recording rules and long-audio windows](docs/dev/speech-production.md#align-supplied-audio)
cover format conversion, optional trim and the 300-second per-window limit.

## Revise without model calls

Render and alignment both produce reusable Units with their own audio, transcript,
alignment and integrity hashes. Export a plan from either completed run:

```console
regrain speech plan runs/tts-v1 --output assembly.json
regrain speech assemble assembly.json --output runs/revised-v1
```

Between the commands, edit `assembly.json`: change `pause_after_ms`, reorder the
`units` array, or copy a replacement's `artifact` and `sha256` together from
another exported plan. Keep plans in the same directory when copying references.
[Take replacement and human/AI mixing](docs/dev/speech-production.md#replace-a-take-or-mix-human-and-ai-units)
explain the full procedure. All selected Units must share a sample rate; replacement
recordings need their own exact alignment. No loudness matching is automatic.

## Reuse a legacy run

For a retained, completed pre-v0.2 run without Unit manifests:

```console
regrain speech import-run runs/legacy --output runs/imported
regrain speech plan runs/imported --output legacy-plan.json
regrain speech assemble legacy-plan.json --output runs/legacy-revised
```

Modern render/alignment/assembly runs can export plans directly. Legacy import
starts integrity hashes from the observed bytes; it does not reconstruct historical
authenticity. See [import and explicit failure recovery](docs/dev/speech-production.md#failure-recovery-and-old-runs).

## Use the result

A complete media run contains `report.json`, reusable `units/`, and
`final/final.wav`, `final/final.srt`, `final/timeline.json`, `final/.complete`.
Check the command's JSON status and completion marker, then review listening and
subtitle timing. Failed/incomplete runs are diagnostic artifacts; reuse successful
Units through an explicit repair plan. See the [artifact contract](docs/dev/speech-production.md#artifact-contract-and-publication).

Regrain and Revisual evolve independently. Revisual integration is deferred and
does not block Regrain release readiness. The stable artifact/exchange contract,
including the existing `media-pipeline` consumer identifier, is preserved in
[naming and migration](docs/naming.md).

## Status, release and development

[CURRENT](docs/CURRENT.md) owns implemented capabilities, Owner-tested workflow
evidence and open problems. Those observations do not establish exact-wheel
release acceptance; each rebuilt artifact has its own [release gates](docs/release.md).

For development, use `uv sync --python 3.12` and
`uv run python scripts/run_tests.py fast`. [AGENTS](AGENTS.md) routes contributors
to the relevant contracts; [test profiles](docs/dev/testing.md) distinguish CPU,
GPU and Owner checks. [ROADMAP](docs/ROADMAP.md) is non-binding planning.

The repository is [Yang-Zhaowei/regrain](https://github.com/Yang-Zhaowei/regrain).
Historical records and exact SHAs remain in their linked evidence files.

## License

No license has been selected yet.
