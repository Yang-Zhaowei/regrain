# v0.2.0 production-workflow candidate

The [merged PR #17](https://github.com/Yang-Zhaowei/media-pipeline/pull/17) is
the v0.1.0 predecessor and its owner acceptance remains
historical evidence for that exact candidate only. It does not accept this
v0.2.0 candidate, its new workflow behavior, or any rebuilt wheel. The owner
controls review, merge, tagging, release and deployment. Keep the pull request
in Draft until the owner completes review.

The [naming migration](naming.md) is a breaking change. Use the
[production workflow guide](dev/speech-production.md) for the current artifact,
assembly and external-audio alignment interface. Existing model and accepted
worker-environment paths remain unchanged.

## Build and identify

From a clean, committed checkout, create a separate build environment (Python
3.10+), install `hatchling==1.27.0`, and run:

```console
python scripts/prepare_release.py --output dist/REPLACE_WITH_FRESH_ARTIFACT_DIRECTORY
```

The script refuses dirty source and existing artifact names, uses the commit
timestamp, checks package/distribution version agreement, and produces the
`regrain-0.2.0` wheel and `regrain-0.2.0-manifest.json`. Keep both together: the
manifest records source commit/tree, Python/build versions, size and SHA256;
`--version` reports `0.2.0`. The script publishes nothing. For reproducibility,
retain the recorded build-environment versions and compare two builds of the
same clean commit locally.

The wheel contains the `regrain` package, runtime adapters, production Skill and
examples. Experiments, checkpoints and private assets are not shipped.

## Verification before owner review

```powershell
.venv/Scripts/python.exe -m pytest -o addopts=-ra -q
$env:REGRAIN_TEST_WHEEL = (Resolve-Path ./dist/REPLACE_WITH_FRESH_ARTIFACT_DIRECTORY/regrain-0.2.0-py3-none-any.whl).Path
.venv/Scripts/python.exe -m pytest tests/test_packaging.py -o addopts=-ra -q
git diff --check
```

Set `REGRAIN_TEST_WHEEL` equivalently on other shells. Without it, installed-wheel
tests explicitly skip rather than building or downloading during a unit test
run. They inspect packaging, validate shipped examples and exercise installed
CLI and isolated CPU workers outside the checkout. CPU tests and real GPU
acceptance are separate. Also run Skill lint and local documentation-link checks
when available. Do not change immutable fixtures.

## Exact-artifact acceptance and release

1. Review the complete Draft PR, including package, CLI, configuration,
   artifacts, migration steps, tests and known limits. Leave readiness and merge
   to the owner. PR #17 is historical context only; preserve its original
   evidence and SHA without transferring acceptance.
2. After the intended source is accepted and merged by the owner, build the
   exact clean source into a new wheel and manifest. Record that source commit,
   wheel SHA256 and manifest as a new candidate identity. Keep the prior
   v0.1.0 wheel and installation intact for rollback.
3. Follow [deployment verification](deployment.md#owner-only-core-candidate-installation)
   for that exact v0.2.0 wheel. On ai-core, verify installed CLI validation,
   render, reusable artifact assembly, replacement audio, and external-audio
   alignment with real runtime assets. Confirm output WAV/SRT/timeline, input
   integrity, completion markers and failure behavior. Record the exact wheel
   hash and real GPU results. CPU fakes and installed-wheel tests do not satisfy
   this gate.
4. Record owner listening and subtitle/timeline review for relevant generated
   outputs. The earlier PR #17 owner acceptance cannot satisfy these new gates.
5. Only after exact-artifact GPU and human acceptance and explicit owner
   approval may the owner tag and publish `v0.2.0`, its wheel, manifest, release
   notes and acceptance evidence. A rebuild creates a new artifact identity and
   requires its own acceptance.

No merge, tag, release or deployment is performed by this candidate workflow.
