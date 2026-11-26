import sqlite3

from synthora import store
from datetime import UTC, tzinfo


def _when(timestamp: str, tz: tzinfo) -> str:
    return store.local_time(timestamp, tz).strftime("%Y-%m-%d %H:%M")


def build_status(conn: sqlite3.Connection, tz: tzinfo = UTC, limit: int = 10) -> str:
    counts = store.count_by_status(conn)
    summary = " · ".join(f"{s}: {n}" for s, n in sorted(counts.items())) or "none yet"
    lines = [f"📊 Captures: {summary}"]

    waiting = sum(counts.get(s, 0) for s in store.PENDING)
    pending = store.pending_captures(conn, limit)
    lines += ["", f"⏳ Waiting for the Vault ({waiting})"]
    lines += [
        f"• {_when(r['created_at'], tz)}  {r['value'][:60]}" for r in pending
    ] or ["• none"]
    if waiting > len(pending):
        lines.append(f"…and {waiting - len(pending)} more")

    failures = store.recent_failures(conn, limit)
    lines += ["", f"❌ Recent failures ({len(failures)})"]
    lines += [
        f"• {_when(r['created_at'], tz)}  {r['value'][:60]}\n   {r['reason'][:100]}"
        for r in failures
    ] or ["• none"]
    return "\n".join(lines)
