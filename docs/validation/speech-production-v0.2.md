# v0.2.0 production workflow candidate validation

Baseline: `main` at `1176b9e5bf67b4959058af2b4a2e94bfc2453b24` (merged PR #17).
Implementation is a Draft candidate. The old candidate's owner GPU evidence is
historical and cannot be transferred to this source or its wheel.

## CPU verification on 2026-10-11

Executed from the Windows Controller development environment:

```console
.venv/Scripts/python.exe -m pytest -o addopts=-ra -q --tb=short
```

Result: **822 passed, 14 skipped**. Skips: six unconfigured real GPU integration
cases, three installed-wheel cases before setting `REGRAIN_TEST_WHEEL`, three
torch serialization cases (torch absent), and two Windows symlink cases (creation
privilege absent). No GPU model inference ran. These are deterministic processing,
CPU fake model/subprocess protocol and fixture regressions, not model acceptance.

New coverage includes:

- A real portable 15-Unit render with fake TTS/Aligner, followed by pause-only
  revision without model calls; exact PCM, frame offsets, SRT and provenance.
- Single/multiple replacement Units from different runs and different durations;
  unchanged Units' PCM/alignment/text preserved, one-time SRT rounding, half-up
  44.1 kHz pauses and subsequent revisions after moving original assets.
- Full recording PCM/silence preservation and explicit contiguous/gapped frame
  windows, transcript coverage, duration bounds, opt-in trim and common artifact
  interface; one fake Alignment session, no TTS.
- Missing/duplicate/unexpected child results, nonzero child exit with successful
  prefix, runtime and per-Unit semantic errors, failure recovery, input mutation,
  malformed/truncated/stereo/rate-mismatched WAV and strict JSON validation.
- Unit hashes/pins/markers/path confinement, manifest/transcript/metadata tamper,
  source changes during snapshot, exact published-byte verification and completion
  failures including exceptions after marker write.
- Legacy completed-run import reconstructs old Final PCM/SRT/timeline; missing or
  altered Final/Unit data cannot complete. Integrity history starts at import.
- New naming/CLI/environment contracts and rejection of legacy runtime aliases.

Installed-wheel and final source results are recorded in the Draft PR after
building from a clean committed source. The wheel/manifest remain local ignored
build outputs; neither a tag nor release is created. Record its exact source/hash
before any owner test. A different rebuild has its own acceptance boundary.

## Owner ai-core acceptance required

Use a fresh versioned Controller with the reviewed wheel/manifest and existing
TTS/Alignment environments; follow [deployment](../deployment.md) and
[migration](../naming.md). Record actual commands, input and asset hashes,
runtime/checkpoint versions, logs, durations, artifacts and human observations.
Do not describe CPU fakes or skipped integration tests as GPU results.

1. Verify installed `regrain` version/import/CLI, new settings and worker package
   bootstrap with existing environments. Run real CustomVoice and Clone speech
   production, including an approved 15-Unit episode and at least two unequal
   performance-unit lengths. Confirm one TTS and one Alignment session.
2. Export/edit pauses and assemble with model runtime settings unset. Verify all
   Unit PCM/alignment hashes stay unchanged, exact silence/frame offsets and
   WAV/SRT/timeline sync. This operation is CPU-only even on ai-core.
3. Select one and multiple better takes from another real run. Reassemble and
   listen for seams, volume, timbre/delivery and subtitle sync. Check unchanged
   Unit PCM exactly and replacement timing/provenance. Regrain does not stabilize
   Clone delivery or master mismatched volume; Issue #8 remains open.
4. Align an owner-recorded full narration with accurate transcript, an external
   TTS clip and a short replacement recording using `speech align`. Verify zero
   TTS loads, one Alignment session, exact selected source PCM in preserve mode,
   standard Final artifacts and manually reviewed subtitle accuracy.
5. Test a recording longer than 300 seconds using caller-defined contiguous frame
   windows and exact per-window transcripts. Listen/check boundaries, natural
   pauses and long-window resource use. Confirm full PCM reconstruction, source
   coordinates and global integer offsets. The advertised upstream limit does
   not prove accuracy at that limit in the installed runtime.
6. Exercise a transcript mismatch and a runtime/output failure; retain reports,
   verify no valid Final marker and explicitly reassemble valid Units plus a
   corrected replacement. Structural alignment acceptance is not ASR proof.
7. Import a retained v0.1 completed episode without changing it, revise/assemble,
   then roll executable/config/Skill selection back to the accepted v0.1 setup.
   Do not relocate models/environments or modify revisual.

The owner controls readiness, merge, release and deployment. This PR remains
Draft pending exact-artifact GPU and human acceptance. No new modality, service,
NLE integration, external repository change or deployment automation is included.

Known limits are in the [production contract](../dev/speech-production.md): explicit
long-audio text windows, PCM16/common-rate input, in-memory whole-source decoding,
no acoustic mismatch detection or automatic loudness/quality correction, no
in-place resume or automatic salvage of TTS-only intermediates. Windows symlink
guards need validation on a host that can create them; POSIX runtime behavior
must be verified on ai-core. No Linux/macOS execution is claimed by this CPU run.
