# Client / Core installation and rollback

Use Python 3.10+ (accepted Core runtime: 3.12). Install the Controller separately
from the existing TTS and Aligner environments; neither worker needs another
regrain installation. See the [runtime/CLI contract](dev/render-entry-point.md)
for package loading and [release gates](release.md) for exact-artifact acceptance.

Keep the development checkout, versioned Controller environments, model environments,
checkpoints, private voice assets and episode directories separate. For example:

```text
/srv/ai/src/regrain/                         development / experiment replay
/srv/ai/apps/regrain/controllers/           versioned Controller environments
  0.1.0-<source-sha>/                       prior accepted version; preserve for rollback
  0.2.0-<source-sha>/                       new breaking-change candidate
  current -> 0.2.0-<source-sha>/             optional, only after owner acceptance
/srv/ai/apps/media-pipeline/tts/.venv/       existing accepted TTS environment
/srv/ai/apps/media-pipeline/aligner/.venv/   existing accepted alignment environment
/srv/ai/models/speech/                      existing checkpoints
/srv/ai/episodes/<episode>/                 speech.json, voices/, runs/
```

These are examples, not application defaults. Do not rename the accepted model
environment directories for cosmetic consistency. No SSH, transfer, package update,
checkpoint download or GPU process management is automated by regrain.

## Client installation and authoring

Use one shared CLI installed from the verified local wheel via
[uv tools](https://docs.astral.sh/uv/concepts/tools/), with
[uv-managed Python](https://docs.astral.sh/uv/guides/install-python/). No global
Python or per-Agent/per-content-project regrain installation is needed. Compare
the hash with the matching manifest first; in PowerShell outside the checkout:

```powershell
Get-FileHash ./regrain-0.2.0-py3-none-any.whl -Algorithm SHA256
uv python install 3.12
uv tool install --managed-python --python 3.12 --no-index ./regrain-0.2.0-py3-none-any.whl
regrain --version
regrain speech validate ./episode/speech.json
```

An Agent subprocess may retain an old PATH after installation. Restart the host
process, or invoke the absolute executable path without reinstalling:

```powershell
$regrainExe = Join-Path (uv tool dir --bin) 'regrain.exe'
& $regrainExe --version
& $regrainExe speech validate ./episode/speech.json
```

Keep one reusable [regrain-speech folder](../skills/regrain-speech/SKILL.md),
including examples, in a compatible Agent Skills discovery location. Codex and Pi
can consume that same copy when configured to discover it; otherwise register its
path with each host. The owner's Client uses `C:\Users\zhaowei\.agents\skills\regrain-speech`.
Other hosts may use different paths. The wheel also ships this folder under
`regrain/skills/regrain-speech`. Confirm host discovery and validator access;
the [reported Client acceptance](validation/regrain-v0.1.0-owner-acceptance.md)
does not prove all hosts' discovery mechanisms.

For a Client update, verify the new wheel/manifest, then use the same local-wheel
install command with `--reinstall` (also for same-version candidate changes).
Retain the previous verified wheel/manifest for rollback by reinstalling that
wheel. Refresh the shared Skill from the selected version and restart hosts as
needed. Do not edit uv's tool environment with pip. No development PYTHONPATH is needed.

Review the validator's JSON plan and obtain human approval of the exact input.
Manually transfer speech.json and authorized clone assets while preserving their
relative directory relationship. Static validation does not check asset existence,
checkpoint compatibility or audible quality. Runtime/model paths belong in Core
local configuration, not in speech.json.

## Owner-only Core candidate installation (uv)

The owner performs these steps after reviewing the exact v0.2.0 artifact. Use
**versioned `uv venv` + `uv pip install` on Core**, not a global `uv tool`
installation and not `uv run` against a checkout. The installed CLI is executed
directly; uv is the environment manager. No existing TTS/Aligner environment,
model or accepted Controller is changed. See the [migration guide](naming.md).

Use the actual artifact directory and the source commit from its manifest.
For example, the artifact may live under
`/srv/ai/artifacts/regrain/pr18-<source-prefix>/`; the following shell variables
are **placeholders**, not hard-coded installation defaults. Run the following
as a **standalone installation script**; it enables `set -eu` to stop on failed
verification. Do not paste that line into an interactive SSH login shell, where
an expected validation failure could terminate the session.

```sh
set -eu
artifact_dir=/path/to/reviewed/artifacts
controller_root=/srv/ai/apps/regrain/controllers
release_id=0.2.0-REPLACE_WITH_SOURCE_SHA_PREFIX
candidate="$controller_root/$release_id"
wheel="$artifact_dir/regrain-0.2.0-py3-none-any.whl"
manifest="$artifact_dir/regrain-0.2.0-manifest.json"

uv --version
uv python find 3.12

# Verify wheel bytes/size and explicit source identity before installation.
uv run --no-project --no-python-downloads --python 3.12 python - "$manifest" "$wheel" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
wheel = Path(sys.argv[2])
digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
assert manifest["project"] == "regrain"
assert manifest["version"] == "0.2.0"
assert manifest["source_commit"] == "REPLACE_WITH_FULL_SOURCE_SHA"
assert wheel.name == manifest["wheel"]["filename"]
assert wheel.stat().st_size == manifest["wheel"]["bytes"]
assert digest == manifest["wheel"]["sha256"]
print("Verified", manifest["source_commit"], digest)
PY

# Refuse any existing directory or symlink; never upgrade an accepted venv.
test ! -e "$candidate" && test ! -L "$candidate"
mkdir -p "$controller_root"
uv venv --python 3.12 --no-python-downloads "$candidate"
uv pip install --python "$candidate/bin/python" --offline --no-deps --link-mode copy "$wheel"
uv pip check --python "$candidate/bin/python"
cp "$manifest" "$candidate/regrain-manifest.json"
unset PYTHONPATH PYTHONHOME
"$candidate/bin/regrain" --version
"$candidate/bin/python" -c 'import regrain; print(regrain.__file__)'
"$candidate/bin/regrain" speech validate /path/to/episode/speech.json
```

Do not install Regrain again in the TTS/Aligner venvs. Keep the exact manifest
beside this installation, and keep the original wheel/manifest in the immutable
artifact archive. An existing directory means stop and create a **new**
version/source-labelled Controller; do not overwrite. `--offline` and
`--no-deps` prevent package downloads and model dependency installation; the
v0.2.0 Controller has no required runtime dependencies.

### Core runtime configuration and daily execution

Model and worker paths are **Core-local**. Loading
`/srv/ai/config/regrain/runtime.env` is explicit; Regrain does not read this
file automatically. Export all variables so a CLI subprocess can see them:

```sh
set -a
. /srv/ai/config/regrain/runtime.env
set +a

# This file should include these local values (illustrative paths):
# REGRAIN_TTS_PYTHON=/srv/ai/apps/media-pipeline/tts/.venv/bin/python
# REGRAIN_ALIGNMENT_PYTHON=/srv/ai/apps/media-pipeline/aligner/.venv/bin/python
# REGRAIN_ALIGNMENT_MODEL=/srv/ai/models/speech/asr/Qwen3-ForcedAligner-0.6B
# REGRAIN_TTS_MODEL=/srv/ai/models/speech/tts/Qwen3-TTS-12Hz-1.7B-Base
# REGRAIN_VOICE_REGISTRY=/srv/ai/assets/voices/regrain

test -x "$REGRAIN_TTS_PYTHON"
test -x "$REGRAIN_ALIGNMENT_PYTHON"
RG="$candidate/bin/regrain"
"$RG" --version
```

Without exported `REGRAIN_TTS_PYTHON`/`REGRAIN_ALIGNMENT_PYTHON`,
`speech render` stops at configuration preflight even if `--tts-model` is set.
Explicit CLI flags override these variables. Source this file in **each new
shell/session** or use a separately maintained launcher that loads it; do not
assume SSH, uv or Regrain persists shell environment. Prefer direct executable
paths in automation and avoid leaving `set -e` enabled in interactive SSH
sessions, where a nonzero CLI result can close the shell.

Run with the accepted existing interpreters and checkpoints, using fresh
output directories:

```sh
"$RG" speech render /path/to/clone-episode/speech.json \
  --tts-model /srv/ai/models/speech/tts/Qwen3-TTS-12Hz-1.7B-Base \
  --output /path/to/clone-episode/runs/0.2.0-candidate-clone
"$RG" speech render /path/to/custom-voice-episode/speech.json \
  --tts-model /srv/ai/models/speech/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice \
  --output /path/to/custom-voice-episode/runs/0.2.0-candidate-custom
```

These are real GPU workflows invoked by the lightweight Controller, **not**
`uv run` commands. Changing an episode's `--tts-model` does not require
reinstalling any Python package; audio assembly itself does not need model
settings.

For external audio, validate the prepared alignment request and run
`regrain speech align request.json --output <new-run-dir>` with the alignment
runtime options. To reuse successful units, import the prior run, create and
review an assembly plan, then assemble into a new run directory. See the
[production workflow guide](dev/speech-production.md) for command contracts.
Preserve the owner's desired local configuration rather than changing global
environment files.

**Required GPU gate for the selected artifact:** installed CLI workflows run
on ai-core with the accepted separate interpreters and real assets/checkpoints.
The [merged PR #17](https://github.com/Yang-Zhaowei/media-pipeline/pull/17) is
the v0.1.0 predecessor; its original owner acceptance remains
historical and does not transfer to the v0.2.0 wheel. Record the selected
artifact SHA256, source commit, runtime/model/asset identities, commands, exit
codes and reports. Check complete rendering, pause-only assembly without model
calls, cross-run unit replacement, external-audio alignment, final WAV/SRT/
timeline, input immutability, failure states and human listening/sync. CPU fakes
cannot satisfy this gate.

Only a complete render publishes `final/final.wav`, `final/final.srt` and
`final/timeline.json`; retain `report.json` and the release manifest. Failed or
incomplete runs are diagnostic artifacts. Manually transfer complete media back
to Client for revisual, FFmpeg or the chosen NLE. Those tools consume documented
artifacts and are not Controller dependencies or internal import consumers.
See the [render/artifact contract](dev/render-entry-point.md).

## Promote, upgrade and rollback

Prefer invoking the accepted directory explicitly. An optional Linux current
symlink can be switched after owner acceptance (and when no run is active):

```sh
ln -s "$candidate" "$controller_root/current.next"
mv -Tf "$controller_root/current.next" "$controller_root/current"
```

`current.next` must not already exist; `current` must be absent or a symlink, not
a real directory. Record the previous target before switching. This example uses
GNU/Linux tools; the full environment path is portable and sufficient elsewhere.

For an upgrade, create another version/source directory, install its wheel, and
repeat the static and GPU gates before switching. Keep the previous environment,
manifest and local configuration. Roll back by invoking the previous environment
directly or switching the symlink back with the same two commands and its previous
target. Rollback needs no PyTorch reinstall or model download. Existing outputs,
model directories and worker environments remain usable. Never edit code inside
accepted installations.
