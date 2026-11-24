import sqlite3

from synthora.server.database import store


def _when(timestamp: str) -> str:
    return timestamp[:16].replace("T", " ")  # '2026-11-23 07:09' (UTC)


def build_status(conn: sqlite3.Connection, limit: int = 10) -> str:
    counts = store.count_by_status(conn)
    summary = " · ".join(f"{s}: {n}" for s, n in sorted(counts.items())) or "none yet"
    lines = [f"📊 Captures: {summary}"]

    waiting = sum(counts.get(s, 0) for s in store.PENDING)
    pending = store.pending_captures(conn, limit)
    lines += ["", f"⏳ Waiting for the Vault ({waiting})"]
    lines += [f"• {_when(r['created_at'])}  {r['value'][:60]}" for r in pending] or [
        "• none"
    ]
    if waiting > len(pending):
        lines.append(f"…and {waiting - len(pending)} more")

    failures = store.recent_failures(conn, limit)
    lines += ["", f"❌ Recent failures ({len(failures)})"]
    lines += [
        f"• {_when(r['created_at'])}  {r['value'][:60]}\n   {r['reason'][:100]}"
        for r in failures
    ] or ["• none"]
    return "\n".join(lines)
