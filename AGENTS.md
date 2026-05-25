Always use the OpenAI developer documentation MCP server if you need to work with the OpenAI API, ChatGPT Apps SDK, Codex, Agents SDK, Responses API, or other OpenAI product details without me having to explicitly ask.

## CodeGraph

This project uses CodeGraph for local code indexing and dependency analysis.

- The graph database is at `.codegraph/codegraph.db`.
- Codex should use the `codegraph` MCP server when available for symbol search, call relationships, impact analysis, and project structure queries.
- If the graph is stale, run `codegraph sync .`; if it is missing, run `codegraph init --index .`.
