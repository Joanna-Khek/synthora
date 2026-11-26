---
status: superseded by 0004 (the Reader is rule-based; the Extraction Server is kept and called by rules)
---

# Reader agent calling tools on an Extraction MCP Server

The Reader is an LLM agent rather than a fixed Source Type → extractor lookup. Its extraction methods (trafilatura, Defuddle, og-meta, GitHub API, YouTube metadata) are exposed as tools by a separate **Extraction Server** built on the Model Context Protocol (MCP). The bot process launches the server over stdio, and the Reader connects to it with `langchain-mcp-adapters`. The agent picks a tool from the URL and a rule-based Source Type hint, and tries another tool when a result is empty or poor. This handles pages that one extractor mangles (e.g. LinkedIn comments under trafilatura) without hand-coding every fallback chain. Putting the extractors behind MCP keeps them out of the agent code, makes them testable on their own (e.g. with MCP Inspector), and lets other MCP clients reuse them later.

## Considered Options

- **Fixed lookup in Python (Source Type → extractor function)** — simplest, fastest and cheapest, but every fallback chain is hand-written. Kept as the fallback if the agent misses the latency target (NFR-2).
- **Agent with tools defined directly in LangGraph (no MCP)** — fewer moving parts, but the extractors are tied to one agent framework and can't be reused by other clients.
- **Remote MCP server over HTTPS** — reusable from any Claude app, but needs auth and a public endpoint. Deferred (PRD §9.10).

## Consequences

- Each Capture costs at least one extra LLM call. The Reader is limited to 5 tool calls and uses a small model.
- Fetched pages are untrusted input to an agent. The Reader may only fetch the Capture's own URL, all tools are read-only, and the Extraction Server refuses private and loopback addresses (PRD FR-4c, NFR-3).
- Node.js is needed on the server for Defuddle.
- Images bypass the Extraction Server and go straight to the model's vision input.
