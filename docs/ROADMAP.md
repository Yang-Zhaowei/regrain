# Roadmap

Status: planning / non-binding. [CURRENT](CURRENT.md) owns implemented
capabilities, acceptance boundaries and open problems; [AGENTS](../AGENTS.md)
defines agent scope. Milestone history and exact evidence live in CURRENT and
its linked validation records rather than a second status list here.

## Project role

regrain provides small, local-first, verifiable media operations where a real
production workflow exposes a gap not adequately served by existing tools.
It reuses complete capabilities first and builds only the demonstrated remainder,
stopping once the gap is closed. It is not a mandatory execution/evidence layer,
complete NLE, media framework, workflow engine or Agent control platform.

## Next decisions: artifact acceptance and production evaluation

PR #18's speech production workflows are merged and the owner reported successful
real workflow use. Exact-wheel acceptance for merged source remains a separate
[release gate](release.md); revisual integration is separate, deferred work and
does not block Regrain release readiness. These are
verification/owner decisions, not a mandate for new development.

Continue evaluating the owner's [podcast workflow](workflows/podcast-pilot.md)
with existing tools. A useful pilot has one actual finishing tool and Agent host,
real source media, narration/captions, at least one revision and a reviewed final
export. Evaluate long-form delivery under [Issue #8](https://github.com/Yang-Zhaowei/regrain/issues/8)
without assuming a root cause or universal optimal unit length.

Try existing capabilities first:

1. NLE-native functionality;
2. official APIs / extensions;
3. maintained integrations / plugins;
4. established tools and standards;
5. regrain code for the remaining demonstrated gap.

The evaluation ends when either no additional development is needed or one
reproducible capability gap is identified. Both outcomes are successful.
Downstream integration work needs its own owner authorization.

## Feature admission rule

A new feature requires:

1. a real production input/problem;
2. a reproducible inadequacy in existing solutions;
3. evidence that regrain is the correct ownership boundary;
4. the smallest implementation that closes the gap;
5. a stopping condition;
6. consideration of maintenance cost and an exit/replacement path.

"Could be useful", "fits the architecture", and "might be needed by an
Agent later" are not sufficient reasons.

Reproducibility means the path runs and its checks are repeatable; model stages
need not produce byte-identical output. Determinism is reserved for input rules,
timing, format conversion and verification.

## Uncommitted questions

Media inspection/extraction, ASR, Vision/media search, timeline interchange,
NLE integration, MCP/HTTP and orchestration are not roadmap milestones. Each
must pass the admission rule before any project code. Vision and ASR remain
un-proposed until a real case re-enters that gate.

## Long-term success

Success does not require the project to grow. A small, reliable speech tool that
remains useful beside existing NLEs and Agent integrations is a valid outcome.
