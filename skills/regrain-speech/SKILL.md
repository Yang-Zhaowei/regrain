---
name: regrain-speech
description: Author, validate, render and revise approved speech with Regrain. Use for narration scripts, recording alignment, reusable Units, editable Assembly and legacy run import.
---

# Regrain speech workflows

Use the installed `regrain` CLI for the requested speech operation. Preserve
caller-approved text, voice, timing and source choices. Existing authorization
applies; ask for review when a proposed change goes beyond it. Keep private assets
and Core runtime/checkpoint paths out of scripts and source control.

## Choose the operation

| Starting point | Route | Input |
| --- | --- | --- |
| Approved text; preset or reusable clone voice | [Narration](#narration) | `speech.json` |
| Recording and exact spoken transcript | [Recording](#recording) | `alignment.json` + WAV |
| Existing Units; pause/order/take changes | [Revision](#revision-and-legacy-import) | Exported assembly plan |
| Completed old run without Unit manifests | [Legacy import](#revision-and-legacy-import) | Old run directory |

Run `regrain --version` and the chosen subcommand's `--help` when needed. TTS
rendering uses Core's TTS and Aligner Workers; recording alignment uses only the
Aligner. Plan, assembly and legacy import are model-free. Use fresh output run
names and plan files; do not overwrite or mutate source Units.

## Narration

Start from [CustomVoice](examples/custom-voice.json) or [clone](examples/clone.json),
copied into the content project as `speech.json`.

- Put spoken words in `text`; keep edit/visual notes outside the script.
- Author coherent Performance Units for semantic, performance or revision reasons.
  Preserve approved text; do not silently rewrite, split, merge, truncate or omit it.
  The default ceiling is 200 Unicode code points per Unit, not an optimal length
  or model limit. Subtitle boundaries are generated independently.
- Select exactly one `speaker` or `voice` source. Clone uses a trusted relative
  asset inside the script directory and only omitted/exactly empty `instruct`.
  CustomVoice can inherit top-level direction, clear it with `""`, or override it.
  Do not add experimental sampling controls as production fields.

```console
regrain speech validate speech.json
regrain speech render speech.json --output runs/narration-v1
```

For authoring-only requests, stop after validation. Render when it is part of the
requested or already authorized work.

Static validation does not load/check clone assets, checkpoints or audible quality.
If overriding `--max-segment-chars`, use the same approved budget for validate and
render. Core selects CustomVoice or Base models through runtime configuration.
Transfer approved scripts/assets manually when Client and Core are separate.

## Recording

Copy [recording.json](examples/recording.json) as `alignment.json` beside the
recording. Set each segment's `text` to its exact spoken transcript, not a filename.
Input must be nonempty mono PCM16 WAV; `preserve` keeps selected PCM and natural
pauses. Convert other formats outside Regrain. For audio over 300 seconds, use
explicit frame windows and exact per-window transcripts from the production guide.

```console
regrain speech align alignment.json --output runs/recorded-v1
```

`align` checks its own request; `speech validate` is only for narration scripts.
Forced alignment is not ASR or an acoustic proof that the transcript is correct.
Review sync; do not infer, repair or invent missing spoken text.

## Revision and legacy import

```console
regrain speech plan runs/narration-v1 --output assembly.json
regrain speech assemble assembly.json --output runs/revised-v1
```

Between commands, edit pauses/order or replace a Unit's `artifact` and `sha256`
together using another exported plan. Export plans beside one another so relative
references have the same base; rebase paths if a plan moves. Keep IDs unique and
sample rates equal. A replacement brings its own transcript/alignment; arbitrary
WAVs and copied old alignment are not reusable Units. Regrain does not match gain
or rank takes automatically.

A modern run already owns Units. Only a completed legacy run without manifests
needs import first:

```console
regrain speech import-run runs/legacy --output runs/imported
regrain speech plan runs/imported --output legacy-plan.json
regrain speech assemble legacy-plan.json --output runs/legacy-revised
```

## Results and recovery

Validation succeeds with exit 0 / `valid`; media-producing commands require
`complete` and a valid Final marker before handoff. Plan export only creates a
JSON plan. Preserve original CLI errors and `report.json`. Failed/incomplete runs
are diagnostic: repair missing Units into a fresh run and explicitly assemble
successful pins plus replacements; never silently drop requested content. There
is no in-place resume or automatic retry. Review final WAV, subtitles and timeline.

## Authoritative references

These repository URLs remain usable when this folder is copied or shipped in a
wheel; the examples above travel with the folder. Read only the relevant contract:

- [Narration, voices, CLI options and exit codes](https://github.com/Yang-Zhaowei/regrain/blob/main/docs/dev/render-entry-point.md)
- [Units, recording windows, replacement/mixing and recovery](https://github.com/Yang-Zhaowei/regrain/blob/main/docs/dev/speech-production.md)
- [Client/Core configuration and Skill discovery](https://github.com/Yang-Zhaowei/regrain/blob/main/docs/deployment.md)
- [Naming and stable exchange identifiers](https://github.com/Yang-Zhaowei/regrain/blob/main/docs/naming.md)
- [Current capability and evidence](https://github.com/Yang-Zhaowei/regrain/blob/main/docs/CURRENT.md)
- [Exact-artifact release gates](https://github.com/Yang-Zhaowei/regrain/blob/main/docs/release.md)

Install/register the whole Skill folder with the Agent host's supported discovery
mechanism; use the copy from the selected Controller version. Regrain does not
transfer files or require Revisual. Revisual integration is deferred independently
and does not block Regrain readiness; preserve its compatibility identifier.
