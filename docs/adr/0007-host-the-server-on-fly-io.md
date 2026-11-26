---
status: accepted
---

# Host the server on Fly.io: one 256 MB machine in Singapore

The always-on half of Synthora (ADR 0001), the Telegram bot with its Extraction Server as a child process, runs as one Docker image on **Fly.io**, in the `sin` (Singapore) region, close to the owner (UTC+8). It runs on exactly **one** shared-cpu-1x machine with 256 MB of memory, and keeps its SQLite queue on one Fly **volume**. The account is on Fly's Legacy Hobby plan, whose free allowances (up to three shared-cpu-1x 256 MB machines, 3 GB of volume storage, 30 GB of outbound transfer in Asia Pacific) cover this deployment, as long as it fits in 256 MB. Memory is measured locally (`docker stats`) before relying on that.

## Considered Options

- **A Hetzner VPS (the plan's original path)**: the cheapest paid option, and full control; but the owner administers the machine (users, SSH, firewall, security updates) and runs Caddy for HTTPS. Rejected: Fly's existing free allowances cost nothing, and remove most of that work.
- **Fly.io**: deploys the Dockerfile as is, provides HTTPS on `*.fly.dev`, restarts crashed machines and snapshots volumes daily. Chosen.

## Consequences

- **Exactly one machine, never scaled out.** A Fly volume attaches to a single machine and SQLite lives on it, and Telegram allows only one bot to poll. Two machines would split the queue and fight over updates.
- **Always on while the bot polls.** `auto_stop_machines` must stay off: a polling bot never looks idle. After 8.3 switches the bot to a webhook, Fly could start the machine on demand instead, which would be cheaper; that is decided then.
- **Secrets are Fly secrets** (`fly secrets set`), injected as environment variables at runtime; they are never in the image or in `fly.toml`.
- **Memory is the risk.** The bot and the Extraction Server are two Python processes, and Defuddle starts Node.js per LinkedIn post. If they don't fit in 256 MB: add swap (`swap_size_mb`), then reduce memory (e.g. the `mlflow-tracing` package instead of full MLflow), and only then pay for a 512 MB machine.
- **No Caddy and no server hardening steps**: Fly terminates TLS and manages the host. What stays the owner's job: the app's own security (the owner gate, the pull API's bearer token, NFR-3) and backups of the SQLite file beyond Fly's daily volume snapshots.
- **The laptop runner can't read the container's database**: once the bot runs on Fly, new Captures reach the laptop only through the pull API (8.4) and an `HttpQueue`. Until then, the local bot stays the one in real use.
