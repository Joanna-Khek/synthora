---
status: superseded by 0008 (the Cartographer runs on the server and writes vault files; Obsidian Headless syncs them, so no laptop is needed)
---

# Always-on bot server with laptop-side Obsidian CLI

Synthora is split in two: an always-on server receives Captures from Telegram, runs the Reader and Distiller, replies with the TL;DR and queues the result; a process on the user's laptop pulls that queue over authenticated HTTPS, runs the Cartographer and writes to the Vault via the official Obsidian CLI, then acknowledges each item. The split exists because Telegram discards undelivered bot updates after 24 hours (so the bot cannot live on a laptop that sleeps), while the Obsidian CLI only works against a running Obsidian desktop app (so Vault writes cannot happen on a headless server).

## Considered Options

- **Everything on the laptop** — rejected: Captures lost whenever the laptop is off for more than 24 hours, and no instant TL;DR.
- **Everything on a server, writing vault files directly (file-based MCP such as mcpvault) and syncing via git** — viable and fully headless, but loses the CLI's backlinks/links queries the Cartographer relies on; rejected in favour of the official CLI.

## Consequences

- The laptop process must run on the Windows side (where Obsidian runs), not inside WSL, because the CLI talks to the app over a local socket.
- Vault changes appear only when the laptop is on with Obsidian open; `/status` exposes what is still queued.
- The full extracted Source text is retained on the server so the Cartographer can be re-run without re-fetching.
- If Obsidian Headless (see the `obsidian-headless` skill in obsidianmd/obsidian-skills) proves able to support server-side Vault writes, this decision should be revisited.
