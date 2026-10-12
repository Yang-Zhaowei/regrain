# Verification profiles

Run profiles from the repository root with the uv-managed development Controller
(`uv sync --python 3.12` once, then `uv run python ...`). Model packages are
not required for Fast or Full CPU verification. Plain `uv run python -m pytest`
keeps the original all-collected behavior, including opt-in skips; nothing is
silently removed from that command. The installed production Wheel on ai-core is
a separate versioned Controller, not this development environment.

| Profile | Coverage and requirements |
| --- | --- |
| Fast | All in-process CPU integrity, exception, frame/pause/caption, naming, workflow and immutable fixture regression cases. Excludes explicitly listed process-isolation, installed-wheel, optional Torch and GPU cases. |
| Full | Fast plus fresh-process worker/CLI/import-isolation and real isolated wheel installation. Requires a wheel and manifest matching the clean current commit/tree and exact file SHA256. Optional Torch serialization cases skip when Torch is absent. Excludes GPU inference. |
| GPU | Existing real TTS/Alignment and end-to-end integration cases, using the configured separate runtime interpreters. Missing runtime configuration fails before dispatch. This is source integration, not installed-wheel or listening acceptance. |
| Owner | Full and configured GPU checks, with the same exact-artifact gate. Automated success still requires the manual installed-wheel production scenarios, listening, subtitles, consumer import and rollback checklist. |

```console
uv sync --python 3.12
uv run python scripts/run_tests.py fast
uv run --with hatchling==1.27.0 python scripts/prepare_release.py --output dist/FRESH_CANDIDATE
uv run python scripts/run_tests.py full --wheel dist/FRESH_CANDIDATE/regrain-0.2.0-py3-none-any.whl --manifest dist/FRESH_CANDIDATE/regrain-0.2.0-manifest.json
uv run python scripts/run_tests.py gpu
```

Commit changes before build/Full and check `git status --short` for a clean
checkout. The pinned `uv run --with hatchling==1.27.0 python` build supplies
the backend without adding Hatchling to the project's persistent runtime
dependencies. A stale, changed or mismatched wheel cannot pass the Full/Owner
candidate gate. Owner uses the same wheel/manifest arguments
as Full plus the [runtime configuration](../../validation/README.md) and
[human production checklist](../validation/speech-production-v0.2.md). The runner
does not mark a candidate accepted, Ready, released or deployed.

Pass normal pytest options after the profile, for example `--durations=20`,
`--junitxml=PATH` or `-k CASE`. A filtered run verifies only that selected subset;
it cannot replace the complete profile's evidence. `uv run python -m pytest -m full`
runs only additional CPU cases; `-m 'not gpu'` runs Fast plus Full. These direct
commands do not perform the candidate manifest gate.

The tier registry is explicit in `scripts/pytest_tiers.py`, registered by
`tests/conftest.py`. It avoids name heuristics and leaves ordinary collection
unchanged. Add new fresh-process/expensive acceptance cases to the registry (or
the existing dedicated modules); keep the default Fast class for portable
in-process correctness. Do not hide failure or timing coverage behind GPU gates.
For a fair old/new Fast comparison, load the same plugin against an archived tree:

```console
uv run python -m pytest -p scripts.pytest_tiers -c BASELINE/pyproject.toml BASELINE/tests -m fast
```

This archived-tree example assumes the baseline source is accessible to the
current test runner; do not use its result as exact-Wheel acceptance.

Shared deterministic model fakes live in `tests/support.py`, not another test
module. Their bounded waveform cache stores immutable tuples; tests still create
fresh output directories, exercise production writes and independently verify
artifacts. Installed-wheel tests share one module-scoped isolated Controller and
retain actual process/CLI checks outside the checkout. If uv is already available,
they use a no-pip venv and offline, uncached wheel installation. Otherwise they
retain the stdlib venv/ensurepip + pip path. uv is optional; no runtime dependency
was added. Installer configuration variables are cleared for isolation.

See [measured test/product costs and limitations](../validation/pr18-review-performance.md).
Test speed and product operation speed are reported separately. CPU fakes and
synthetic benchmarks never count as real model acceptance. Do not change immutable
regression fixtures to make checks faster.
