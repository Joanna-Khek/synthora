---
status: accepted
---

# Package layout: split by domain and process, imports go one way

`src/synthora` is split into packages that each own one part of the system, and imports only point inward: `bot → capture → domain`, `extraction → domain`, and `obsidian → domain`. **`domain`** holds the shared vocabulary from CONTEXT.md (Capture, Source Type and its URL rules, ExtractedSource, Distillation, ExtractionError) and does no I/O. **`capture`** runs a Capture: the graph, the Reader's `ExtractionClient`, the Distiller, the link picker and `CapturePipeline`, which owns the Repeat Capture check, the database row's lifecycle and the MLflow span. **`extraction`** is the Extraction Server, a separate process. **`bot`** is only the Telegram adapter: it parses messages and renders a `CaptureOutcome`. **`store`** is the SQLite queue (it imports only `domain`), and **`obsidian`** is the laptop-side Vault code (ADR 0001). `config`, `prompts` and `tracing` are shared helpers any package may import. The layout follows ADR 0001's two-process split, so the laptop runner (M9) can import `domain` and `obsidian` without pulling in Telegram, LangGraph or the extractors.

The first layout (`server/`, `agent/`, `obsidian/`) grew a dependency in the wrong direction: the agent imported the Source Type rules from `server/bot`, the laptop-side `obsidian` imported `server/schemas`, and the bot process loaded trafilatura just to get `ExtractionError`. The whole Capture pipeline also lived inside the Telegram handler, so its rules could only be tested through Telegram objects.

## Considered Options

- **Keep `server/`, `laptop/`, `shared/`** (the M0 plan) — matches the deployment split, but says nothing about what is inside each, and `server/` had become a grab bag of bot, graph, store and extractors. Rejected.
- **Layer by technical role** (`schemas/`, `services/`, `handlers/`) — familiar, but one feature change touches every layer, and the layers don't line up with the two processes. Rejected.
- **Split by domain and process, one-way imports** — each package changes for one reason, and the process boundaries from ADR 0001 are visible in the imports. Chosen.

## Consequences

- `domain` must never import from another `synthora` package. Anything both processes need, such as an error type both sides of MCP raise, goes there.
- `capture` must not import `bot` or `extraction`; it reaches the Extraction Server only through MCP, via `ExtractionClient`.
- `obsidian` must import only `domain` (and `config`), so the laptop runner stays free of server-side dependencies.
- Nothing enforces these rules yet; a `grep` for `synthora.bot` or `synthora.extraction` under `src/synthora/capture` should find nothing. If they start to slip, add an import-linter contract to pre-commit.
- `tests/` mirrors the packages (`tests/domain`, `tests/capture`, `tests/bot`, …).
