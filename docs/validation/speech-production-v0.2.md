# v0.2.0 production workflow validation record

Original implementation baseline: `1176b9e5bf67b4959058af2b4a2e94bfc2453b24`
(merged PR #17). The CPU record below was produced while PR #18 was a Draft.
PR #18 subsequently merged on 2026-10-13 (Asia/Shanghai) at
`5d71a2c284649bb4b02ea33d0189f693872081a6`. [CURRENT](../CURRENT.md) owns
current status; this page preserves historical results and acceptance boundaries.

## PR #18 owner workflow observations

The [PR #18 description](https://github.com/Yang-Zhaowei/regrain/pull/18)
records owner-reported ai-core production results, rather than independently
replayed tests:

- Real Clone and CustomVoice rendering completed successfully. Earlier Clone
  rendering exposed a relative-output-path bug; fixes are recorded at `306982d`
  and `33b1764`. Exact successful commands and relative-path replay need provenance.
- Legacy run import, plan export, pause edits and cross-run take selection/
  assembly succeeded without regenerating unchanged Units.
- A 38-second human recording with its transcript was forced-aligned; human
  and AI Units were assembled together with output judged usable by the owner.

Reported production candidate identity:

- Source: `33b176480f6754add8ea433d163bc46733c458ee`.
- Tree: `a6b6c3c6f02d64783749a532567876e113bf84eb`.
- Wheel SHA256: `2707c13f40780901ab9ce80757de6a37d6044341bd34badab62715bf902281f0`.
- Core archive: `/srv/ai/artifacts/regrain/pr18-33b17648/` (reported host path).

The final PR head `f6d8e72e0714e0b456608a0bc8ada5c03a37ebf9` and merged source
have different identities from that candidate. PR checklist completion alone
does not independently bind the successful scenarios to a merged-main wheel.
The manifest/file correspondence, exact execution parameters, logs, listening
and versioned rollback records still need to be retained for the selected artifact.
Use [release gates](../release.md); earlier PR #17 acceptance also stays with
its original source/wheel.

Revisual integration remains deferred. Static producer/consumer contract review
and Final byte parity are recorded in the [CPU review](pr18-review-performance.md),
but actual mixed-Final consumer import is not established. The owner observations
above do not establish 300-second window accuracy, multi-minute Clone stability,
or every failure/recovery scenario in the checklist below.
That consumer follow-up is independent of Regrain's own release gates; its
deferral is not a release blocker. See the [release boundary](../release.md#independent-release-boundary).

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

Later CPU and installed-wheel results are recorded in
[PR #18](https://github.com/Yang-Zhaowei/regrain/pull/18) and its
[engineering review](pr18-review-performance.md), with their own source/wheel
identities. Record the selected artifact's exact source/hash before any owner
test; a different rebuild has its own acceptance boundary.

## Exact-artifact owner checklist

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

The owner controls readiness, merge, release and deployment. Merged PR #18 does
not waive exact-artifact GPU and human acceptance. No new modality, service,
NLE integration, external repository change or deployment automation is included.

The subsequent [PR #18 engineering review and CPU performance measurements](pr18-review-performance.md)
supersede the original Controller memory limitation; original test/owner evidence
above remains historical, with no acceptance transferred to a rebuilt wheel.

Known limits are in the [production contract](../dev/speech-production.md): explicit
long-audio text windows, PCM16/common-rate input, eager public sample-list helpers
and per-window trim/model-worker memory,
no acoustic mismatch detection or automatic loudness/quality correction, no
in-place resume or automatic salvage of TTS-only intermediates. Windows symlink
guards need validation on a host that can create them; POSIX runtime behavior
must be verified on ai-core. No Linux/macOS execution is claimed by this CPU run.
