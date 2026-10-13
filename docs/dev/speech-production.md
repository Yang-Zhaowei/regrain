# Speech production workflows

Regrain v0.2.0 uses one verified speech Unit contract for Render, external
alignment and legacy import. Units own their WAV, exact transcript, language,
precise local alignment and provenance. The independent assembler consumes
ordered Units and pauses without loading TTS or Aligner. Each revision uses a
fresh output directory. See [naming migration](../naming.md) for breaking changes.

## Choose a workflow

| Starting point | Commands | Models |
| --- | --- | --- |
| Approved narration text | `speech validate` → `speech render` | TTS + Aligner for render |
| Recording and exact transcript | [Align supplied audio](#align-supplied-audio) | Aligner only |
| Existing Units; pause, order or take changes | [Render once and revise](#render-once-and-revise) | None for plan/assemble |
| Completed legacy run without Unit manifests | [Import the old run](#failure-recovery-and-old-runs), then plan/assemble | None |

Run the commands below from one content-project directory. All run directories
and exported plan files must be new. Model settings are configured separately in
the [deployment guide](../deployment.md#core-runtime-configuration-and-daily-execution).
`speech validate` validates narration scripts only; alignment requests and
assembly plans are checked by their own commands.

## Render once and revise

Start with the [CustomVoice](../../skills/regrain-speech/examples/custom-voice.json)
or [clone](../../skills/regrain-speech/examples/clone.json) example, copied as
`speech.json`. Use approved text and a checkpoint-supported speaker, or a trusted
clone asset placed at the script-relative path. See the
[render/input contract](render-entry-point.md) for voice and instruction rules.

```console
regrain speech validate speech.json
regrain speech render speech.json --output runs/original
regrain speech plan runs/original --output revision.json
regrain speech assemble revision.json --output runs/revised
```

Plan export works with render, external alignment, imported and assembled runs.
It exports original order/pauses and exact manifest SHA256 pins. Edit pauses,
order or replace one/many references with those from another exported plan.
Assembly reuses audio/alignment; local captions are deterministically compiled
from precise alignment and only global offsets change. For another revision,
export a plan from `runs/revised`.

Between `plan` and `assemble`, open `revision.json`: change `pause_after_ms` to
adjust extra silence, or reorder its `units` array. Keep the exported `artifact`
and `sha256` together. A Unit is its owned WAV/transcript/alignment bundle, not an
arbitrary WAV; the SHA256 pins the exact `unit.json` manifest.

### Replace a take or mix human and AI Units

For a new TTS take, save its approved script as `replacement.json`, render it and
export its plan beside `revision.json`:

```console
regrain speech render replacement.json --output runs/better
regrain speech plan runs/better --output better-plan.json
```

For a human take, use `recorded-plan.json` from the
[recording workflow below](#align-supplied-audio) instead. Copy a selected entry's
`artifact` and `sha256` into the destination plan, keeping its revision `id` and
desired pause, or add it as a new entry with a unique `id`. Plans exported in the
same directory share the same reference base; if a plan moves, rebase its relative
paths. Then assemble into a fresh run. The selected Unit supplies its own exact
transcript and alignment. Human and AI Units use the same contract and must have
the same sample rate; listen for volume/timbre seams after assembly.

The following illustrates plan fields; normally use real pins from `speech plan`:

```json
{
  "schema_version": 1,
  "units": [
    {"id": "opening", "artifact": "runs/original/units/UNIT/unit.json", "sha256": "REPLACE_WITH_MANIFEST_SHA256", "pause_after_ms": 350},
    {"id": "detail", "artifact": "runs/better/units/UNIT/unit.json", "sha256": "REPLACE_WITH_OTHER_MANIFEST_SHA256", "pause_after_ms": 200}
  ]
}
```

Replace placeholders with real lowercase 64-character SHA256 values from exported
plans; they are not valid example pins. References resolve from the plan file's
directory, including `../` across runs. Absolute/drive/URI paths are rejected;
input slash/backslash separators work, generated paths use slashes. A plan and
source assets must share a drive on Windows. IDs are nonempty unique revision
labels, independent of source labels. Unknown fields and boolean numeric values
are rejected. `pause_after_ms` defaults to zero, is a nonnegative integer and
adds silence after the Unit's existing audio, including optional final tail silence.

All Units must have the same sample rate. There is no implicit resampling,
recropping, fade, gain, crossfade or speed change. A replacement's own transcript
and validated alignment travel with its audio. Copying an old alignment onto a
new recording is not supported; align the replacement first.

## Align supplied audio

Copy the [recording example](../../skills/regrain-speech/examples/recording.json)
as `alignment.json` beside your `recording.wav`, or create this request. Replace
`text` with the exact spoken transcript; it is inline text, not a transcript-file
path. A recording uses this alignment request, not a narration `speech.json`.

```json
{
  "schema_version": 1,
  "language": "Chinese",
  "processing": "preserve",
  "segments": [
    {"id": "recorded", "audio": "recording.wav", "text": "这是本人录制的旁白。"}
  ]
}
```

```console
regrain speech align alignment.json --output runs/recorded
regrain speech plan runs/recorded --output recorded-plan.json
```

Configure only `REGRAIN_ALIGNMENT_PYTHON`/`REGRAIN_ALIGNMENT_MODEL` or matching
`--alignment-python`/`--alignment-model` flags. `--device` defaults to `cuda:0`.
The existing Aligner loads once for all windows; no TTS, ASR or new model is used.
Review the reported Final and subtitle sync before reusing its Units. There is
no separate `speech validate` mode for recording requests.

```python
from regrain import align_speech, assemble_speech, create_assembly_plan

align_speech("alignment.json", "runs/recorded", alignment_python="...", alignment_model="...")
create_assembly_plan("runs/recorded", "recorded-plan.json")
assemble_speech("recorded-plan.json", "runs/reassembled")
```

Inputs must be nonempty mono uncompressed PCM16 WAV. Stereo, float/24-bit,
compressed/truncated audio and differing rates fail validation. Convert outside
Regrain and align the converted audio. `preserve` is default: selected PCM samples,
leading/trailing/interior silence are preserved exactly. Whole recordings keep
their original time origin; selected windows start at local zero with original
coordinates recorded in provenance.
`trim` explicitly applies existing speech trim/padding/fade and adjusts alignment;
raw windows remain retained. Both paths create the same Unit schema.

For long audio, supply caller-defined `start_frame` inclusive / `end_frame`
exclusive windows and each window's exact transcript. Both frame fields must be
present together; otherwise the whole file is selected. Indices use source sample
rate. Each window is at most 300 seconds: upstream
[Qwen ForcedAligner](https://github.com/QwenLM/Qwen3-ASR) advertises up to five
minutes, which does not establish this candidate's GPU acceptance. Regrain does
not guess text/audio boundaries or automatically partition transcripts.

```json
{
  "schema_version": 1,
  "language": "Chinese",
  "segments": [
    {"id": "first", "audio": "long.wav", "start_frame": 0, "end_frame": 2400000, "text": "第一窗口的准确讲稿。"},
    {"id": "second", "audio": "long.wav", "start_frame": 2400000, "end_frame": 4800000, "text": "第二窗口的准确讲稿。"}
  ]
}
```

At 24 kHz these are contiguous 100-second windows. Contiguous preserve windows
with zero extra pauses reconstruct selected source PCM exactly. Same-file windows
must be ordered/nonoverlapping. Gaps explicitly omit source material; separate
recordings can be ordered freely. Extra pauses are distinct from existing silence.
Provenance stores source audio hash, original source frame range/rate, processing
policy and raw alignment hash. Global offsets refer to the delivered Final;
source ranges retain original recording coordinates through later revisions.

Text is never rewritten. Timestamp bounds/order, deterministic transcript coverage
and usable caption/SRT durations are checked. Missing, nonfinite, overlapping,
wrong-text or unusable alignment fails. Forced alignment may still force an
incorrect supplied transcript onto speech and pass structural checks. Provide
accurate text and manually review subtitle sync; this is not acoustic correctness
detection. No estimated timestamps, silent omissions or mismatch repair are used.

## Artifact contract and publication

```text
run/
  request.json
  report.json
  segments/                 stage diagnostics where applicable
  units/UNIT_DIRECTORY/
    unit.json
    audio.wav
    alignment.json
    .complete               exact unit.json SHA256
  final/
    final.wav
    final.srt
    timeline.json
    .complete               literal complete; atomically published last
```

Unit schema 1 has `kind: speech_unit`, `text`, `language`, `audio` with path/hash/
sample_rate/frames, `alignment` with path/hash, `provenance`, and `content_id`.
The content ID hashes canonical manifest content excluding itself; marker and
plan pin bind exact manifest bytes. Internal asset paths must remain inside the
Unit, including symlinks. Hashes identify changed bytes and stale relationships;
they are not signatures or acoustic quality judgments. Strict JSON rejects
duplicate keys, NaN/Infinity. Source files are checked, copied and checked again.
Failed Units have no valid marker. Compiled local captions must also be
non-overlapping; an unassemblable Unit cannot receive a completion marker.
Treat Python-returned arrays as read-only;
assembly revalidates pinned disk assets.

An assembled run owns complete snapshots, so originals can move without breaking
later revisions based on the new run. Unit provenance retains initial lineage;
assembly report sources store selected manifest digest/content ID and the original
request Unit index. Original authored references remain in `request.json` as
historical input, while `unit_manifest` always resolves relative to the new run.
Snapshots preserve exact manifest/audio/alignment bytes and their pins. Verified
metadata is reused only within the current operation. Source and copied bytes
are still hashed, markers checked, and the destination independently validated;
there is no persistent cache based on file timestamps.

Final's stable exchange contract keeps the existing paths and timeline shape: `sample_rate`,
`total_frames`, `start_seconds`, `duration_seconds`, ordered `segments` containing
`segment_id`, `start_frame`, `audio_frames`, `pause_after_frames`. Report segments
add `unit_manifest` and `unit_sha256`. Revisual is one optional independent
consumer; its integration is deferred and does not block Regrain release
readiness. Preserve its existing `media-pipeline` request tag as documented in
[naming](../naming.md); it is not a Regrain Python/CLI alias.

Assembly sums actual integer audio frames and half-up pause frames, shifts
unrounded local captions by `start_frame / sample_rate`, and quantizes SRT once.
Staged and published files are reread and checked against exact hashes, WAV
metadata, caption text/timing and frame timeline before complete report/marker.
Per-unit millisecond rounding never accumulates into global offsets.
The literal Final marker becomes visible through atomic replacement only after
validation and the complete report. Its bytes remain `complete` for existing
consumers. This is an atomic visibility guarantee, not a crash-durability promise.

## Failure recovery and old runs

All outputs must be fresh; inputs are immutable. A failed/incomplete report or
missing/invalid Final marker means no completed episode even if files exist.
Run-level exceptions keep reasons and run paths; semantic per-window failures
yield `incomplete` and no Final. Completed independent Units remain reusable.
External alignment also validates and saves successful prefix Units before
reporting a later model/subprocess fault. I/O and unclassified faults stop the run.

Recovery is explicit: repair/regenerate/record failed Units into a fresh run,
then author the full ordered plan from successful pins and replacements. Plan
export refuses any missing requested Unit and never silently shortens an episode.
There is no in-place resume, automatic retry, arbitrary-WAV discovery or implicit
cache. A TTS-only intermediate still needs alignment before it becomes reusable;
Render does not automatically salvage raw audio after a model-stage fault.

```console
regrain speech import-run runs/old-v0.1 --output runs/imported
regrain speech plan runs/imported --output revision.json
regrain speech assemble revision.json --output runs/revised
```

Legacy import requires complete report/marker, checks requested identity/order/text,
validates cleaned audio and adjusted alignment, reconstructs original Final PCM/
SRT/frame timeline and snapshots Units. Hashes start at import; provenance says
`bytes_observed_at_import`. Historical authenticity cannot be reconstructed.
Retain the untouched old environment/wheel/episode for rollback.

Speech CLI JSON remains schema 1: success 0; usage/config/output errors 2;
invalid render/external/assembly inputs 3; incomplete 4; started render/align/
assembly/import failures 5. Import preflight errors use 2.
`regrain captions` keeps its existing SRT-output interface.

Controller assembly, legacy PCM comparison and external recording preflight/
window snapshots use bounded PCM blocks (65,536 frames), with metadata and
caption memory proportional to Unit/token count. Assembly writes each verified
Unit and deterministic silence directly into the staged Final, then publishes
by same-filesystem rename. External preflight records each window's PCM hash;
copied windows must match before any Aligner call, including when a changed
source is restored between boundary file-hash checks.

Public `SpeechUnit.samples`, `load_unit`, `create_unit`, `snapshot_unit`,
`load_assembly_plan` and `load_audio_request` retain their eager list defaults.
Callers using these helpers still allocate sample lists. Explicit `trim` still
materializes one window in the existing postprocessor, and model workers retain
their existing memory behavior. The 300-second bound applies to an individual
Aligner request, not GPU memory or total job duration. No automatic VAD/transcript
splitting, gain matching, ASR or quality ranking is included. Unit schema and
processing strings are version boundaries. See [measured CPU costs and review](../validation/pr18-review-performance.md)
and [owner GPU checklist](../validation/speech-production-v0.2.md).
