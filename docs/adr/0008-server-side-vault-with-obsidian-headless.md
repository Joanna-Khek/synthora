---
status: accepted
date: 2026-11-26
supersedes: ADR 0001 (the laptop half), ADR 0006 (where the Content Server runs)
---

# Server-side Vault, synced with Obsidian Headless

The whole pipeline now runs on the Fly.io machine (ADR 0007), including the Cartographer. The Vault is a folder of plain Markdown files on the Fly volume (`/app/data/vault`), and the Cartographer writes to it through the `files` Vault backend (`FileVault`) instead of the Obsidian CLI. **Obsidian Headless** (`ob sync --continuous`, npm package `obsidian-headless`) keeps that folder in sync with the owner's Obsidian Sync vault, so new notes reach the laptop and phone, and the owner's edits come back, without any device being on. This answers PRD §9.9 and the "revisit" note in ADR 0001.

## Context

ADR 0001 split Synthora in two because the Obsidian CLI only works against a running Obsidian desktop app. As a result, Vault writes only happened when the laptop was on with Obsidian open, and Captures sat in the queue until then. Obsidian Headless syncs a vault without the desktop app, so the server can own the Vault and Obsidian Sync carries it to every device.

## Considered Options

- **Keep the laptop runner (ADR 0001)**: the CLI gives native search and backlinks, but notes only appear when the laptop is on with Obsidian open. Rejected, because "my notes are waiting for my laptop" works against G2 and G3.
- **Server writes files, synced with git**: fully headless, but the phone needs a git client, and every edit made in Obsidian has to be committed and pushed by hand. Rejected.
- **Server writes files, synced with Obsidian Headless**: uses the Obsidian Sync the owner already relies on across devices, needs no device to be on, and edits flow both ways. Chosen.

## Decision

- `start.sh` runs, in one container: MLflow, the pull API, `ob sync --continuous`, a loop that starts `synthora-runner` every 60 seconds, and the bot.
- The runner reads the local SQLite queue (`LocalQueue`) directly, because it runs next to the bot.
- The Vault uses `backend: files`. `VAULT__BACKEND`, `VAULT__PATH` and `VAULT__NAME` are set in `start.sh`, not in `fly.toml`, because Fly drops environment variables whose names start with `VAULT`.
- The Content Server (ADR 0006) still runs as a stdio child process of the runner, now on the server. Its tools and their safety rules are unchanged.
- `ob`'s login is stored under `HOME=/app/data/home` on the volume, so it survives deploys. Log in once with `fly ssh console`.
- `obsidian-headless` is pinned in `package.json` (currently 0.0.14).

## Consequences

- **Notes appear within about a minute, on every device, whether the laptop is on or off.** `/status` now mainly reports Failed Captures, not a backlog that waits for the laptop.
- **No Obsidian CLI on the server.** `FileVault` reimplements search, aliases, backlinks and property updates by reading the Markdown files. These must stay consistent with what Obsidian itself would resolve (FR-13, FR-14), and they are covered by tests rather than by the app.
- **The busy-note check (FR-25) relies only on modification time.** There are no open tabs to inspect on a server (`open_note_names` is empty). A note the owner edited on any device within `vault.busy_seconds` (120 s) syncs down with a recent mtime and is treated as busy. Edits still being typed and not yet synced are invisible, so Obsidian Sync's conflict handling is the last line of defence for those.
- **The machine needs more memory.** The bot, the Extraction Server, the runner with the Content Server, MLflow, `ob sync` (Node.js) and Defuddle (Node.js) share one machine. ADR 0007's 256 MB machine was too small, so it was raised to 2 GB (`fly.toml`). That leaves Fly's free allowance, which changes the cost picture in NFR-5.
- **Obsidian Sync is now a required, paid dependency,** and Obsidian Headless is young (0.0.x). If its CLI changes, the pin protects deploys, and upgrades are made deliberately.
- **The Vault lives on the Fly volume and in Obsidian Sync**, so there are two copies, plus Fly's daily volume snapshots and Obsidian Sync's version history. Per-Capture git commits (FR-20) stay off.
- **The pull API and `HttpQueue` stay in the code.** The laptop runner (ADR 0001) is still a working fallback if the server-side Vault ever has to be turned off.
- **Docs to update:** PRD §6 (architecture diagram, Laptop runtime and Vault access rows), PRD §9.9 (now resolved), ADR 0001 and ADR 0006 (mark as partly superseded), ADR 0007 (memory size).
