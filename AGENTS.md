# regrain

Local-first speech production toolkit. Start with [README](README.md) for usage
or [current state](docs/CURRENT.md) for capability status, open problems and
evidence. Do not infer release acceptance from merged code or owner observations.

## Read only what the task needs

| Task | Authority |
| --- | --- |
| Author or operate speech workflows | [regrain-speech Skill](skills/regrain-speech/SKILL.md) and [workflow guide](docs/dev/speech-production.md) |
| Imports, CLI/configuration migration, consumer compatibility | [Naming](docs/naming.md) |
| Speech inputs, renderer and CLI behavior | [Render contract](docs/dev/render-entry-point.md) |
| Units, assembly, external alignment and recovery | [Production contract](docs/dev/speech-production.md) |
| Development checks and test profiles | [Testing](docs/dev/testing.md) |
| Build identity and exact-artifact acceptance | [Release gates](docs/release.md) |
| Client/Core installation, runtime configuration and rollback | [Deployment](docs/deployment.md) |
| Future scope and feature admission | [Roadmap](docs/ROADMAP.md) (non-binding) |

`docs/validation/` preserves dated results and exact SHAs; those records do not
define current scope. [GPU Worker environment notes](docs/dev/core-runtime.md)
record host-specific evidence, not portable defaults or a fresh inventory.
`docs/contracts/` retains detailed speech rules linked from the active guides.
`docs/legacy/` is historical architecture, not current requirements.

## Boundaries

Preserve the naming, speech and downstream artifact contracts. Never transfer
acceptance to a rebuilt wheel; use the release guide's exact-artifact gates.
Revisual is an independent optional consumer; its deferred integration does not
block Regrain's own release readiness. Preserve existing compatibility identifiers.
The owner controls Ready status, merge, release, repository rename and deployment.
Do not add new modalities, NLE integration, general orchestration, or other scope unless explicitly requested.

Keep the uv development checkout, invocation-scoped build tools, Client `uv tool`
installation, versioned Core Controller and existing GPU Worker environments
separate. Follow the testing, release and deployment guides for their commands.

## Repository evidence and authoring

- `experiments/`: previously working GPU smoke tests; reference only.
- `tests/fixtures/`: immutable real model outputs used for regression tests.
- `skills/regrain-speech/`: reusable Agent Skill for authoring and validating speech inputs.

## Rules

- Prefer deterministic processing over LLM decisions.
- Pure logic and unit tests must run without CUDA.
- Keep paths portable across Windows, Linux, and macOS.
- Never hard-code host paths, drive letters, tokens, or credentials.
- Separate pure processing from model/runtime integration.
- Do not modify regression fixtures unless explicitly requested.
- Avoid speculative abstractions and unnecessary dependencies.

## Out of scope for now

HTTP API, MCP, ASR workflow, Vision, NLE automation, deployment automation, job queues, and UI.

## Validation

Before finishing a change:
- run relevant tests;
- report tests actually executed;
- distinguish unit tests from real GPU integration;
- do not claim GPU validation unless it ran on ai-core.
