---
status: accepted
supersedes: 0002
---

# Rule-based Reader: one extractor per Source Type

The Reader is plain code, not an LLM agent. The Source Type is decided by URL rules (host and path), and each Source Type maps to exactly one extractor, with no fallback: Web Article → trafilatura, LinkedIn Post → Defuddle, GitHub Repo → GitHub REST API, YouTube Video → YouTube Data API v3. If that extractor fails, the Capture becomes a Failed Capture with the reason. Building the extractors (task 3.1) showed the routing is obvious from the URL, so an LLM choosing between tools adds cost, latency and a prompt-injection surface without making better choices.

The Extraction MCP Server from ADR 0002 is kept as the place the extractors are hosted. The Reader calls its tools through an MCP client (`session.call_tool(...)`), choosing the tool by rules instead of by an LLM. The server holds the API keys and the HTTP client, and the same tools are open to other MCP clients (MCP Inspector, Claude apps, future agents).

## Considered Options

- **Reader agent choosing tools (ADR 0002)** — handles pages one extractor mangles without hand-written fallbacks, but costs an LLM call per tool step and needs URL and call-count guardrails. Rejected: the routing is a lookup.
- **Rule-based routing with a fallback chain** (e.g. trafilatura → Defuddle → og tags) — recovers some failures automatically, but hides which extractor really works and re-fetches dead pages once per extractor. Rejected for now; revisit per Source Type if its Failed Capture rate is high.
- **Rule-based Reader calling plain functions, no MCP server** — simplest, but the extractors could only be used from inside the bot process. Rejected: hosting them as MCP tools makes them reusable and testable from MCP Inspector.
- **LLM Reader with a prompt that names the tool for each Source Type** — the prompt would be a lookup table in English, adding an LLM call and the chance of a wrong tool choice for the same result. Rejected.

## Consequences

- No LLM call before the Distiller, so reading is fast and free, and easy to test with fake HTTP responses.
- A page its one extractor can't read becomes a Failed Capture, and the owner replies with pasted text or a screenshot (FR-22). The Failed Capture rate per Source Type is the signal to add a fallback.
- Misclassified URLs (e.g. a LinkedIn article under `linkedin.com/pulse/`) get the Web Article extractor. Fix by adding a URL rule, not an LLM.
- Prompt-injection guardrails now apply to the agents that read Source text (Distiller, Cartographer).
- `read_og_meta` is not used by the Reader; it stays available as an Extraction Server tool.
- Each Capture pays for an MCP round trip (and, if a session is opened per Capture, a server start-up). Measure it before optimising.
