# regrain

Local-first speech production tools for rendering, reusing, replacing and aligning
audio. The v0.2.0 implementation is merged via PR #18 and packages a lightweight
Controller; PyTorch, `qwen-tts`, and `qwen-asr` stay in their existing runtime
environments. [Current state](docs/CURRENT.md) owns capability status, owner
workflow observations and remaining acceptance gates.

## Install and use

Install a verified local wheel once on Client with `uv tool`, using uv-managed
Python; see [installation and PATH discovery](docs/deployment.md#client-installation-and-authoring).
Then use the shared CLI from any content project:

```console
regrain --help
regrain --version
regrain speech validate speech.json
regrain speech render speech.json --output new-run-dir
regrain speech plan new-run-dir --output assembly.json
regrain speech assemble assembly.json --output revised-run
regrain speech align alignment.json --output recording-run
```

`validate` checks the script without models or input changes. `render` uses Core's
existing runtimes. The [CLI/input contract](docs/dev/render-entry-point.md) defines
runtime settings, outputs and errors. [Speech production](docs/dev/speech-production.md)
defines editable assembly and external audio inputs; [migration](docs/naming.md)
explains the breaking switch to `regrain` imports and `REGRAIN_*` settings.

## Speech workflow

```text
speech.json → validation → TTS → forced alignment → postprocess → captions
            → verified Units → assembly → WAV + SRT + timeline + report
recording + exact transcript → forced alignment → verified Units → assembly
existing Units + replacements + pauses → assembly (no model calls)
```

On PC_Client, an Agent prepares `speech.json`, runs `regrain speech validate`,
and hands the validated plan to a human for approval. The approved input and
required voice assets are transferred manually to ai-core for
`regrain speech render`; the resulting artifacts are transferred back manually
for FFmpeg or NLE finishing. Revisual consumer integration is deferred; see
[current acceptance boundaries](docs/CURRENT.md#owner-observations-and-acceptance-boundary).

The reusable [speech-authoring Skill](skills/regrain-speech/SKILL.md) helps
Agents author inputs and run the official validator. Use one shared Skill copy;
host discovery is configured as described in the deployment guide.

## Release and deployment

- [Client/Core deployment, verification, upgrade and rollback](docs/deployment.md)
- [v0.2.0 build identity and owner acceptance gates](docs/release.md)
- [Project and package naming decisions](docs/naming.md)
- [Current state](docs/CURRENT.md) and [roadmap](docs/ROADMAP.md)

The repository is hosted at
[Yang-Zhaowei/regrain](https://github.com/Yang-Zhaowei/regrain).
Historical PR URLs, tested commit identities, old environments and verification
artifacts are retained as evidence.

## Existing production evidence

The owner reported real PR #18 Clone/CustomVoice rendering, external alignment,
editable assembly and mixed human/AI output. These observations do not establish
exact-wheel acceptance for merged `main`; rebuilt wheels need their own gates.
Clone/long-form consistency (Issue #8) remains open. See the
[current evidence index](docs/CURRENT.md#historical-evidence) and
[v0.2.0 workflow record](docs/validation/speech-production-v0.2.md).

## Development

Contributor and Agent guidance is in [AGENTS.md](AGENTS.md). In a development
checkout, use `uv sync --python 3.12` and
`uv run python scripts/run_tests.py fast` for daily CPU checks. For full
CPU/GPU and exact-Wheel gates, see the
[Fast/Full/GPU/Owner profiles](docs/dev/testing.md). The [release guide](docs/release.md)
owns the clean-commit build command and artifact gates. Production Core uses a
separate versioned uv Controller venv and direct installed CLI; Client uses
`uv tool`. Existing TTS/Aligner GPU environments are independent of both.

## License

No license has been selected yet.
