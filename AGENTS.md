# my-agent Agent Notes

This repo is a Python teaching implementation of an Agents SDK-style runtime: agents, model turns, tools, guardrails, workspace context, sessions, tracing, and verification.

Start with [docs/project_analysis/ARCHITECTURE_INDEX.md](docs/project_analysis/ARCHITECTURE_INDEX.md) for LLM-facing architecture context.

When changing runtime behavior, public symbols, state models, tool contracts, or tests, update the relevant `docs/project_analysis/*` file and append `docs/project_analysis/CHANGELOG.md`.

Code beats docs. If docs and code disagree, trust code and fix the docs.

## Architecture documentation

When module boundaries, entry points, call chains, public interfaces, state, configuration, persistence, or cross-module dependencies change, update `docs/project_analysis/` according to [MAINTENANCE.md](docs/project_analysis/MAINTENANCE.md) and append `docs/project_analysis/CHANGELOG.md`. Preserve unrelated instructions.
