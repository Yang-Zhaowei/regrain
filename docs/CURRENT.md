# Current state

This page owns current capability status, open problems and the evidence index.
Behavior is defined by the [render](dev/render-entry-point.md),
[production](dev/speech-production.md) and [naming](naming.md) contracts.
[ROADMAP](ROADMAP.md) is non-binding planning; dated validation records preserve
what actually ran, rather than granting acceptance to later source or wheels.

## Implemented capabilities

[PR #18](https://github.com/Yang-Zhaowei/regrain/pull/18) merged on
2026-10-13 (Asia/Shanghai) at `5d71a2c284649bb4b02ea33d0189f693872081a6`.
It adds the v0.2.0 production workflows to the PR #17 baseline
`1176b9e5bf67b4959058af2b4a2e94bfc2453b24`:

- `regrain` imports, CLI and `REGRAIN_*` settings replace the legacy names;
  this is a breaking migration. The existing downstream `media-pipeline`
  wire-format tag and WAV/SRT/frame-timeline/completion contracts remain.
- Caller-approved Performance Units render through CustomVoice or reusable Base
  clone TTS, forced alignment, deterministic postprocess and captions.
  Each Unit makes one TTS request; subtitle segmentation is independent.
- Verified Units support take selection, cross-run replacement, pause edits and
  assembly into fresh runs without model calls.
- Recordings with exact transcripts use forced alignment and the same Unit/Final
  contracts. Preserve mode retains selected PCM; long recordings require explicit
  frame windows and corresponding transcripts, at most 300 seconds per window.

Controller assembly and recording preflight use bounded PCM blocks. Public eager
helpers, trim and model workers retain their documented memory limits. There is
no ASR, automatic acoustic mismatch detection, loudness/quality correction,
in-place resume or automatic salvage of TTS-only intermediates. See the
[production contract](dev/speech-production.md) for limits and recovery.
PR #20 subsequently merged at `b0786c6ae0ac88ef9c178940f62b4b2cac2dad8a`:
clone-asset saving now stages bytes and atomically replaces the destination.
Its CPU evidence does not establish new Torch/GPU or exact-wheel acceptance.

## Owner observations and acceptance boundary

The owner reported successful real ai-core Clone/CustomVoice rendering, external
alignment, editable assembly and mixed human/AI output during PR #18. The
[workflow evidence](validation/speech-production-v0.2.md#pr-18-owner-workflow-observations)
records those scenarios, the reported `33b1764` wheel identity and remaining
provenance limits. These observations establish useful workflow evidence; they
do not automatically establish exact-wheel acceptance for merged `main`.

The [PR #18 CPU review/performance record](validation/pr18-review-performance.md)
is separate from GPU inference, listening and installed-consumer acceptance.
Revisual integration is deferred: producer compatibility review and byte parity
are recorded, but an actual consumer import has not been established.
Regrain and Revisual evolve independently. That deferred integration does not
block Regrain's own [release readiness](release.md#independent-release-boundary);
existing artifact shapes and the `media-pipeline` exchange identifier remain.

New source or wheels, including documentation-only rebuilds, require their own
[exact-artifact gates](release.md). Reported candidate installation does not
certify a merged-main wheel for production or release. The owner controls Ready,
merge, release and deployment.

## Open problems and next decisions

- [Issue #8](https://github.com/Yang-Zhaowei/regrain/issues/8) remains open for
  multi-segment emotion/delivery drift and long-form narration stability.
  Original v0.1.0 acceptance found variable Clone consistency; unattended Clone
  production quality was not established. New workflow successes do not resolve it.
- The owner-accepted [Qwen-only Layer 1 experiment](validation/qwen-clone-prosody-acceptance.md)
  found promising continuity for roughly 15-second continuous generation.
  Context reset remains the strongest engineering hypothesis, with
  concatenation/topology confounds; sampling-off alone did not eliminate drift.
  Both greedy listening-repeat PCM comparisons passed in the fixed environment.
  Multi-minute stability and a controlled old/new regression comparison remain
  open; a PR #17 regression has not been ruled out.
- Decide when to select and verify an exact merged-source wheel and when to
  resume downstream revisual evaluation. Neither requires speculative features;
  use the [podcast pilot](workflows/podcast-pilot.md) and [admission rule](ROADMAP.md#feature-admission-rule).

## Historical evidence

Earlier acceptance remains tied to its recorded source/artifact, not this baseline.
Historical repository URLs and runtime paths retain their actual names.

| Milestone | Evidence and identity |
| --- | --- |
| Speech Pipeline v0 (PR #5), then Performance Units (PR #9) | [Pipeline closure](validation/speech-v0-closure.md), [segmented acceptance](validation/segmented-speech-v0-acceptance.md); real ai-core and human acceptance |
| Agent-facing CLI (PR #11 / Issue #10, merged 2026-10-03) | [Owner merge gates](https://github.com/Yang-Zhaowei/media-pipeline/pull/11); real ai-core CLI and human acceptance |
| Reusable Base clone runtime (PR #13 / Issue #12, merged 2026-10-04) | [Closure](validation/voice-clone-closure.md); owner accepted creation, fresh-process reuse and two utterances at `8d14639976c7bdcf4d2c1a6ddce3d9b7672cc200` |
| Segmented clone render (PR #15 / Issue #14, merged 2026-10-04) | [Exact-head evidence](validation/clone-render-acceptance.md); three-unit render, one asset/Base load, final artifacts and human acceptance; delivery drift remained |
| Clone prosody Layer 1 (PR #16, merged 2026-10-09T19:04:43Z) | [Owner acceptance](validation/qwen-clone-prosody-acceptance.md) at implementation HEAD `506b30dd59702348d6ff11bc7fad435acddf3a76`, all 13 matrix runs and blind listening; merge `ff73167436937b3788c297b11d9c4b67235c2f5d` |
| v0.1.0 candidate (PR #17) | [CPU/package record](validation/regrain-v0.1.0-candidate.md), [owner GPU/Client record](validation/regrain-v0.1.0-owner-acceptance.md); original `dbcf0db…` source and recorded wheel only; Client authoring/static validation passed after PATH discovery was corrected |

## Environments and verification

Use the [uv development/test profiles](dev/testing.md), [build identity gates](release.md)
and [Client/Core deployment guide](deployment.md) for operational commands.
The lightweight Controller is separate from the existing GPU Workers;
[ai-core notes](dev/core-runtime.md) preserve reported host/runtime evidence.
Pure processing and CPU tests require no CUDA. Real model checks run separately
on ai-core; the [source E2E driver](../validation/README.md) is not exact-wheel acceptance.
