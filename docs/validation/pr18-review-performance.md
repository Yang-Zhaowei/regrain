# PR #18 independent review and CPU performance

Review baseline: PR #18 HEAD was re-confirmed as
`7d063da3a820ab3cc7818f7d291704dec6239dd5` before changes. All measurements below
use that archived source or the revised source fingerprint recorded in the
[machine-readable results](pr18-performance-results.json). PR #18 was a Draft
candidate during this review. No ai-core inference, owner listening, release or
deployment ran as part of these measurements. PR #18 is now merged;
[CURRENT](../CURRENT.md) owns status and links the separate owner workflow record.

## Findings and disposition

| Classification | Evidence | Result |
| --- | --- | --- |
| Confirmed defect | A valid 50-second WAV with one long alignment token could produce overlapping compiled captions, receive a completed Unit marker, then fail assembly. | Unit validation rejects caption overlap before creating a completed asset. The caption compiler's splitting policy is unchanged. |
| Confirmed defect | Symlink-loop `Path.resolve()` raised `RuntimeError` outside the structured artifact/CLI error boundary. | Convert resolution errors to `ArtifactError`; portable mock and privilege-dependent real-symlink tests cover it. |
| Confirmed defect in the new streamed path | Copying a window onto its own source or hardlink could truncate the source. | Resolve/samefile checks and exclusive destination creation reject aliases before writes. |
| Confirmed defect in the new streamed path | Replacing/restoring a WAV during window copying passed boundary source hashes with different copied PCM. | Pin each window's PCM digest during preflight and check consumed PCM before any Aligner call. Assembly likewise checks the PCM it actually consumes. |
| Confirmed publication gap | The existing consumer accepts an existing marker without validating its content; direct marker writes exposed a partially written file. | Atomically publish the same literal `complete` bytes after artifact/report validation; injected before/after-publication faults retain no marker. |
| Measured bottleneck | Whole-source sample lists, whole-Episode accumulation and repeated decodes amplified 57.6 MB of podcast PCM to roughly 3.7 GiB RSS. | Use bounded WAV scans, streamed window copies and streamed assembly; preserve integer frames and exact PCM. |
| Measured bottleneck | Repeated validation/hash reads and copying the staged Final increased logical file traffic. | Reuse verified metadata only inside one operation, retain full source/destination hashes and destination validation, and rename staged products on the same filesystem. |
| Maintainability improvement | Production previously required eager public sample arrays; test modules shared helpers by importing other test modules. | Add a small private metadata path and bounded WAV I/O; centralize test helpers and explicit verification tiers without changing public eager defaults. |
| Speculative concern / deferred | Frozen public records contain mutable arrays; further streaming of trim/model workers might save memory. | Disk inputs are independently verified; no observed corruption warrants a breaking lazy-array API. Trim/model-worker redesign needs separate evidence and owner GPU validation. |

There is no cross-run/stat-only trust cache. Every new revision owns copied Unit
assets so it remains portable after originals move. Eliminating those copies
would change the accepted recovery/ownership contract. Hashing remains full-byte
validation, not a metadata shortcut. No new runtime dependency or model was added.

## Benchmark method

`scripts/benchmark_speech.py` uses standard-library tooling and fresh child
processes. It measures three uninstrumented trials per operation and reports
their median. A separate instrumented run counts logical application file reads,
writes and copies. These are byte traffic, not physical-disk throughput; the OS
cache is warm/uncontrolled. Counter behavior was checked with known-size direct
reads/writes and a copy.

Timers start after imports and exclude fixture creation and post-operation parity
scans. Peak process RSS is sampled before parity scans, includes interpreter/
imports, and excludes fixture setup. It is process memory, not incremental PCM
allocation or GPU memory. The Windows measurement uses process peak working set;
the portable POSIX fallback uses `resource.ru_maxrss`.

Host: Windows 11, Python 3.12.14, Intel Core i5-12400 (12 logical CPUs), 64 GiB RAM.
Corpora are synthetic mono PCM16 at 24 kHz, with deterministic varied integer
samples and supplied alignment. The short case is 15 Units × 6 seconds of audio;
the podcast case is 24 Units × 50 seconds. Assembly adds a 137 ms pause after
each Unit; the next revision changes pauses to 251 ms. These are Controller
benchmarks, not real TTS/Forced Alignment accuracy or audio-quality acceptance.

Controller preflight selects explicit windows of at most 300 seconds. Its
comparison uses the old eager production path and the new private metadata path;
public `load_unit`/snapshot helpers are measured separately and remain eager.
Every timed/I/O assembly and revision matches baseline Final PCM, WAV, SRT,
Timeline and marker hashes. Unit identity and external-window metadata/PCM
digests are also compared. The original overlapping-caption corpus was excluded
from valid timings and retained as a defect reproducer.

Reproduce from two source checkouts without changing fixtures:

```console
python scripts/benchmark_speech.py --source-root BASELINE_CHECKOUT --work-dir NEW_BASELINE_DIR --trials 3
python scripts/benchmark_speech.py --source-root REVISED_CHECKOUT --work-dir NEW_REVISED_DIR --fixture-root NEW_BASELINE_DIR/fixtures --trials 3
```

The result JSON records source fingerprints, raw trials, counters and parity
hashes. Every invocation needs a fresh work directory. Public eager helpers still
use memory proportional to the selected audio; explicit trim and model workers
retain their previous behavior.

## Test cost and coverage

The original all-collected baseline with its original wheel was **825 passed,
11 skipped in 47.01 seconds**. Skips were six unconfigured GPU cases, three
Torch-dependent cases and two privilege-dependent Windows symlink cases. An
earlier permission-failed attempt is excluded from evidence. The successful
baseline's slowest setup was the installed Controller (7.12 seconds); packaging
setup/cases totaled about 10.63 seconds. Process error/recovery and worker
isolation checks remain valuable and remain in Full.

Applying the same explicit Fast registry to the archived baseline selected
**783 passed, 2 skipped, 51 deselected in 19.74 seconds**. The revised Fast run
selected **802 passed, 3 skipped, 51 deselected in 18.12 seconds**. These are single
serial suite runs on the same host, not three-trial product medians; small timing
differences are sensitive to filesystem and scheduler noise. Fast's main saving
versus the original full suite comes from explicit tier selection. The baseline
had no Fast profile: its number is a retrospective comparison with identical
selection rules.

Full CPU on clean source `ba516b3caef67fbd65a64395d6ab3b9b803085a8` and its new
exact wheel returned **844 passed, 6 skipped, 6 GPU cases deselected in 34.81
seconds**. The wheel SHA256 was
`4c4f37fe2044d1bde8c7561e79d937b8ebae43041324c519f5714a22664c032f`.
Skips were three absent-Torch and three Windows symlink cases. The selected CPU
checks retained installed CLI/examples and pinned worker dependency isolation;
ordinary collection still finds all **856 cases** (previously 836).

With uv available, installed Controller setup measured **0.50 seconds** versus
the baseline's **7.12 seconds**. The no-uv fallback was separately forced and
actually installed/tested the new wheel: **3 passed in 9.98 seconds**, with
**6.05 seconds** of pip/venv setup. This fallback is not included in the optimized
Full timing. The [portable test record](pr18-test-results.json) separates these
single-run suite costs from the three-trial product benchmarks.

No original case/parameter combination was deleted. Twenty additional cases
cover streaming, malformed/truncated PCM, alias immutability, window/gap chunk
boundaries, consumed-PCM races, caption overlap, symlink error translation and
atomic marker publication. One new actual symlink case skips on this host; its
portable error-boundary mock runs. Full retains fresh-process and installed-wheel
coverage. Test helper extraction removes imports of other test modules; cached
waveforms are immutable and bounded. An existing optional uv installer avoids
repeated ensurepip bootstrapping; the pip fallback remains available.

The [profile guide](../dev/testing.md) distinguishes daily checks, exact wheel
verification, configured GPU integration and manual Owner acceptance. Requesting
GPU with absent settings was verified to fail before model dispatch. No GPU test
was actually run. Final Full CPU results and exact candidate identity are also
recorded in the Draft PR after its clean build. Documentation commits/rebuilt
wheels have new identities and need their own installed-wheel checks. A local
development `pytest.exe` uv trampoline failed path canonicalization even outside
the sandbox; module-based pytest/profile runs and fresh installed-wheel CLI checks
succeeded. This console-wrapper observation is not claimed as resolved.

## Product operation results

The revised source fingerprint is
`e3043633c2706ef6090ad042a3caf605e26e25ffc14fd07ec2127d434817ff37`.
RSS values below are MiB; times are median seconds.

| Operation | 90 seconds: time before → after | 90 seconds: peak RSS before → after | 20 minutes: time before → after | 20 minutes: peak RSS before → after |
| --- | ---: | ---: | ---: | ---: |
| Assembly | 1.472 → 0.543 | 307.7 → 25.9 | 13.741 → 1.489 | 3792.3 → 26.5 |
| Next pause revision | 2.187 → 0.465 | 308.8 → 26.2 | 13.447 → 1.449 | 3794.0 → 26.5 |
| Controller external preflight | 0.181 → 0.021 | 125.7 → 25.8 | 1.353 → 0.255 | 1388.6 → 25.7 |
| Public eager Unit load (6/50 seconds) | 0.022 → 0.007 | 30.4 → 30.6 | 0.054 → 0.028 | 80.3 → 80.5 |
| Public eager load + snapshot (6/50 seconds) | 0.152 → 0.055 | 46.8 → 36.1 | 0.431 → 0.109 | 216.9 → 126.2 |

Logical I/O uses decimal MB and includes validation reads, hashes, asset copies,
staging and publication. Small manifest/report traffic is included.

| Operation | 90 seconds: reads before → after | 90 seconds: writes before → after | 20 minutes: reads before → after | 20 minutes: writes before → after |
| --- | ---: | ---: | ---: | ---: |
| Assembly | 147.52 → 69.61 MB | 13.18 → 8.76 MB | 1960.08 → 922.69 MB | 173.22 → 115.44 MB |
| Next pause revision | 147.93 → 69.93 MB | 13.35 → 8.84 MB | 1960.74 → 923.22 MB | 173.48 → 115.58 MB |
| Controller external preflight | 17.28 → 21.60 MB | 0 → 0 | 230.40 → 288.00 MB | 0 → 0 |

The preflight read increase of 25% buys source/window integrity checks without
sample lists. Each assembly still copies about 4.33 MB / 57.65 MB of Unit assets
and exact manifests (45 / 72 copy operations, versus 30 / 48 previously).
Manifest copies replace regenerated manifests; Final publication uses rename,
eliminating another full Final copy. Total reads fall about 53%, writes about
33%. File-open counts fall from 1001 to 455 / 1586 to 716; these are application
opens, not physical device operations. Large-file hashing remains intentional.

## Downstream contract review

Revisual was inspected read-only at commit
[`8f4f6a654e76fa78fb2700a8d6fb38db79eee8c7`](https://github.com/Yang-Zhaowei/revisual/tree/8f4f6a654e76fa78fb2700a8d6fb38db79eee8c7).
Its [speech parser](https://github.com/Yang-Zhaowei/revisual/blob/8f4f6a654e76fa78fb2700a8d6fb38db79eee8c7/src/domain/speech.ts)
requires the existing strict integer-frame timeline shape and usable nonoverlapping
SRT. Its [audio validator](https://github.com/Yang-Zhaowei/revisual/blob/8f4f6a654e76fa78fb2700a8d6fb38db79eee8c7/src/domain/audio.ts)
requires mono PCM16 WAV with matching rate/frame count. Its
[import command](https://github.com/Yang-Zhaowei/revisual/blob/8f4f6a654e76fa78fb2700a8d6fb38db79eee8c7/src/cli.ts)
requires a readable completion marker, but does not validate marker/report content.

The revised producer retains these paths/shapes and literal marker bytes; measured
Final byte parity supports compatibility. The existing `media-pipeline` consumer
format tag stays in place as documented in [naming](../naming.md). No revisual code
or repository was modified. This is source-contract review and producer byte
verification; an actual revisual import was not executed. Consumer runtime import
and listening/subtitle review remain owner acceptance items.

## Remaining acceptance

Use the fresh exact wheel/manifest, never the old candidate's acceptance. Follow
the [release gates](../release.md) and [ai-core checklist](speech-production-v0.2.md):
CustomVoice/Clone runtime bootstrap, full recording and explicit long windows,
trim, cross-run replacement/listening, failed alignment recovery, installed
consumer import, POSIX path/filesystem behavior and upgrade/rollback. This Windows
CPU run cannot establish Linux/macOS execution or real GPU alignment accuracy.
Clone consistency and Issue #8 remain open. Larger postprocessor/model-worker
memory changes are deferred until measured separately.
