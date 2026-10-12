# v0.2.0 build identity and release gates

This guide owns build identity and exact-artifact acceptance, not current project
status. [PR #18](https://github.com/Yang-Zhaowei/regrain/pull/18) is merged;
[CURRENT](CURRENT.md) records owner workflow observations and remaining gates.
The [PR #17](https://github.com/Yang-Zhaowei/media-pipeline/pull/17) v0.1.0
acceptance remains historical evidence for its original candidate only. Neither
merge nor earlier workflow success accepts a rebuilt wheel. The owner controls
review, Ready status, merge, tagging, release and deployment.

The [naming migration](naming.md) is a breaking change. Use the
[production workflow guide](dev/speech-production.md) for the current artifact,
assembly and external-audio alignment interface. Existing model and accepted
worker-environment paths remain unchanged.

## Build and identify

From a clean, committed checkout, use uv to provide the pinned build backend
for this invocation (Python 3.10+, recommended 3.12). No persistent build
venv or manually installed `hatchling` is required:

```console
uv run --with hatchling==1.27.0 python scripts/prepare_release.py --output dist/REPLACE_WITH_FRESH_ARTIFACT_DIRECTORY
```

Run from the repository root after `uv sync --python 3.12`. The repository's
tracked `uv.lock` should remain unchanged; verify `git status --short` before
building. `--with` supplies Hatchling for the build invocation, including the
builder subprocess. The command may resolve/download its pinned build tools on
first use; subsequent invocations normally reuse uv's cache.

The script refuses dirty source and existing artifact names, uses the commit
timestamp, checks package/distribution version agreement, and produces the
`regrain-0.2.0` wheel and `regrain-0.2.0-manifest.json`. Keep both together: the
manifest records source commit/tree, Python/build versions, size and SHA256;
`--version` reports `0.2.0`. The script publishes nothing. For reproducibility,
retain the recorded build-environment versions and compare two builds of the
same clean commit locally.

The wheel contains the `regrain` package, runtime adapters, production Skill and
examples. Experiments, checkpoints and private assets are not shipped.

## CPU verification before owner review

Use the commands in the
[Fast/Full/GPU/Owner profiles](dev/testing.md). `scripts/run_tests.py full` requires
both the clean current commit's wheel and its manifest, validates source/tree/
file identity, and includes real offline installed-wheel checks. Ordinary pytest
remains available without that gate; skipped wheel cases are not installation
acceptance. The GPU profile fails on missing configuration and remains separate
from CPU checks and owner listening.

Plain pytest with `REGRAIN_TEST_WHEEL` alone does not verify source commit/tree
and file digest. Without that setting, installed-wheel
tests explicitly skip rather than building or downloading during a unit test
run. They inspect packaging, validate shipped examples and exercise installed
CLI and isolated CPU workers outside the checkout. CPU tests and real GPU
acceptance are separate. Also run Skill lint and local documentation-link checks
when available. Do not change immutable fixtures.

## Exact-artifact acceptance and release

1. Review the selected candidate's package, CLI, configuration, artifacts,
   migration steps, verification and known limits. Any new PR stays Draft until
   the owner decides readiness and merge. Preserve prior evidence and SHAs
   without transferring acceptance to this candidate.
2. From the exact clean candidate source, build a new wheel and manifest using
   the pinned uv command above, and record source commit, tree and wheel SHA256.
   A documentation-only commit also changes candidate identity. If the owner
   builds again after merge, the merged source commit is a new artifact identity
   and requires its own gate. Keep the prior v0.1.0 wheel and installation
   intact for rollback.
3. Follow [deployment verification](deployment.md#owner-only-core-candidate-installation-uv)
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
