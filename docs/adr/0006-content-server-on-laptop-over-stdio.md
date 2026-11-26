---
status: accepted
---

# Content Server on the laptop over stdio, with guarded write tools

The **Content Server** (FR-27, FR-28) is a separate MCP server, `synthora.content.mcp_server`, that runs on the laptop. The laptop runner launches it as a child process over stdio, the same pattern the bot uses for the Extraction Server, and content agents such as the Cartographer connect to it through `synthora.mcp_tools`, a small adapter that turns MCP tools into LangChain tools (`langchain-mcp-adapters` 0.3.2 requires `mcp<2`, and both servers use mcp 2.x). It exposes read tools (`find_node`, `search_vault`, `read_note`, `list_backlinks`, `relation_vocabulary`) and a small set of narrow write tools (`create_node`, `update_synthesis`, `append_evidence`). Every write goes through `Vault.create`, which never overwrites, or `Vault.edit`, which runs the My Notes guard (FR-18). The server runs on the laptop because the Vault only exists there: the Obsidian CLI needs the running desktop app (ADR 0001). No SQLite tool is needed, because the queue item the laptop pulls already carries the Source's `full_text`. This decides PRD §9.10, §9.12 and §9.13.

## Considered Options

- **One remote server over streamable HTTP** — reachable from the VPS and the laptop alike, but it needs auth, TLS and a public endpoint, and it could only serve Vault tools while the laptop is on anyway. That is cost now for agents that don't exist yet. Rejected for v1.
- **Merge with the Extraction Server** — one fewer process, but the two run on different machines, and FR-28 says Content tools never fetch URLs. Keeping them apart keeps URL fetching and Vault access in different processes. Rejected.
- **Read-only Content Server, with writes left in the Cartographer** — a simpler server, but each future agent would write the Vault on its own, so the My Notes guard and the duplicate-Node check would be enforced in more than one place. Rejected.
- **Laptop, stdio, read and guarded write tools** — no network surface, and one implementation of every safety rule that all agents share. Chosen.

## Consequences

- The Content Server only runs while the laptop is on and Obsidian is open. It fails fast at startup (`Vault._check_identity`) when Obsidian isn't running, instead of serving tools that would all fail.
- Safety rules live in the tools, not the prompts: path checks (only `Sources/`, `Concepts/`, `Entities/`, `.md`), refusing duplicate Nodes by title or alias (FR-13), the Relation vocabulary as a schema enum (FR-16), and the My Notes guard (FR-18).
- Write tools are deliberately narrow. There is no "write any text to any section" tool, and a new kind of write means a new, reviewed tool.
- The Distiller on the VPS can't reach the Content Server. Revisit this ADR, most likely with a streamable HTTP server and auth, if an agent on the VPS ever needs Vault data.
- Package layout (ADR 0005): `content` is a process package, and its imports point inward: `content → obsidian → domain`.
