# Naming and v0.2.0 migration

v0.2.0 deliberately removes the v0.1 compatibility surface. Install a new
versioned Controller; retain the accepted environment and wheel for rollback.

| Surface | v0.1 and earlier | v0.2.0 |
| --- | --- | --- |
| Distribution | `regrain` (earlier `media-pipeline`) | `regrain` |
| Import | `media_pipeline` | `regrain` |
| Console | `regrain`, `media-pipeline` | `regrain` only |
| Module speech CLI | `python -m media_pipeline.cli` | `python -m regrain` or `python -m regrain.cli` |
| Caption compiler | `python -m media_pipeline TEXT ALIGNMENT` | `regrain captions TEXT ALIGNMENT` or `python -m regrain.captions_cli` |
| Runtime variables | `MEDIA_PIPELINE_*`, `MEDIA_ALIGNMENT_*` | `REGRAIN_*` only |
| Packaged Skill | `media_pipeline/skills/regrain-speech` | `regrain/skills/regrain-speech` |

There is no legacy import package, console alias or environment fallback. Explicit
CLI flags override the matching new setting. Old settings alone produce missing
runtime configuration errors. Speech script inputs and render Python argument
names remain the same; use `from regrain import render_speech`.

| Old setting | New setting |
| --- | --- |
| `MEDIA_PIPELINE_TTS_PYTHON` | `REGRAIN_TTS_PYTHON` |
| `MEDIA_PIPELINE_TTS_MODEL` | `REGRAIN_TTS_MODEL` |
| `MEDIA_PIPELINE_ALIGNMENT_PYTHON` / `MEDIA_ALIGNMENT_PYTHON` | `REGRAIN_ALIGNMENT_PYTHON` |
| `MEDIA_PIPELINE_ALIGNMENT_MODEL` / `MEDIA_ALIGNMENT_MODEL` | `REGRAIN_ALIGNMENT_MODEL` |
| Validation `MEDIA_PIPELINE_E2E_RUN_DIR` | `REGRAIN_E2E_RUN_DIR` |
| Validation `MEDIA_PIPELINE_SPEECH_INSTRUCT` | `REGRAIN_SPEECH_INSTRUCT` |

Update callers, local configuration and Skill copies together. See
[validation](../validation/README.md) for opt-in GPU settings. Values can still
point at directories named `media-pipeline`: do not move models, voice assets,
Python environments or episodes for cosmetic consistency. Workers bootstrap the
selected Controller package without another installation in their environments.

## Upgrade and rollback

1. Retain the accepted v0.1 wheel/manifest, environment, old configuration and
   Skill. A version string does not identify an accepted artifact.
2. Build/identify a committed v0.2.0 wheel and install in a fresh versioned
   Controller following [deployment](deployment.md) and [release gates](release.md).
   Do not upgrade accepted Core Python environments in place or mix distributions.
3. Replace imports/commands/settings, keeping runtime values. Check version,
   static validation and installed-wheel tests outside the checkout. The owner
   then runs GPU and human acceptance on that exact wheel.
4. Only the owner switches the executable or `current` pointer after acceptance.
   Roll back by selecting the untouched old Controller and its old settings/Skill.
   New settings cannot configure the old Controller. Client `uv tool` rollback
   reinstalls the retained verified old wheel.

Final paths, WAV/SRT/frame timeline and report/completion conventions remain
compatible for revisual; no other repository is modified. Old runs lack Unit
manifests: use `speech import-run` into a fresh directory, then `speech plan`.
Import validates observed Units and reconstructs the old Final. Integrity hashes
start at import, not retrospectively; retain the untouched old episode.

The downstream revisual request format tag `media-pipeline` is an existing wire
contract, not a Regrain Python/CLI alias. Keep that tag when importing Regrain
speech into revisual: its [schema at reviewed commit 8f4f6a6](https://github.com/Yang-Zhaowei/revisual/blob/8f4f6a654e76fa78fb2700a8d6fb38db79eee8c7/src/domain/schema.ts)
accepts `internal` and `media-pipeline`. Changing the tag to `regrain` would require
a separately authorized consumer migration. This review changed no downstream code.
Regrain and Revisual evolve independently. Revisual integration remains deferred
and does not block Regrain's own release readiness; stable exchange compatibility
is preserved without making that consumer a Controller dependency.

Active code and replay tools use new names. Historical contracts, immutable
fixtures, owner evidence, PR URLs and physical runtime paths keep actual names.
The configured repository is [Yang-Zhaowei/regrain](https://github.com/Yang-Zhaowei/regrain).
This task performs no repository administration, release or deployment.
