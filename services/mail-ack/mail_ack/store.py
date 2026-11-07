"""SQLite JSON document store. Transactions serialize concurrent workers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from mail_ack import transitions


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS docs (path TEXT PRIMARY KEY, body TEXT NOT NULL)")
        finally:
            conn.close()

    def reserve(self, **kwargs) -> dict:
        keys = _reserve_keys(kwargs["message_id"], kwargs["thread_id"], kwargs["sender_key"], kwargs["now"])
        return self._run(keys, lambda docs: transitions.reserve(docs, **kwargs))

    def mark_attempted(self, message_id: str, now: datetime) -> dict:
        day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        keys = [f"msg:{message_id}", f"counter:{day}", "control/latch", "control/inflight"]
        return self._run(keys, lambda docs: transitions.mark_attempted(docs, message_id=message_id, now=now))

    def finalize(self, message_id: str, state: str, now: datetime) -> dict:
        keys = [f"msg:{message_id}", "control/inflight"]
        return self._run(keys, lambda docs: transitions.finalize(docs, message_id=message_id, state=state, now=now))

    def age_attempt(self, message_id: str, now: datetime) -> dict:
        keys = [f"msg:{message_id}", "control/inflight"]
        return self._run(keys, lambda docs: transitions.age_attempt(docs, message_id=message_id, now=now))

    def record_ineligible(self, **kwargs) -> dict:
        keys = [f"msg:{kwargs['message_id']}"]
        return self._run(keys, lambda docs: transitions.record_terminal(docs, state="ineligible", **kwargs))

    def claim_pubsub(self, pubsub_id: str, now: datetime) -> dict:
        return self._run([f"pubsub:{pubsub_id}"], lambda docs: transitions.claim_pubsub(docs, pubsub_id=pubsub_id, now=now))

    def clear_latch(self) -> dict:
        return self._run(["control/latch"], transitions.clear_latch)

    def get(self, key: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT body FROM docs WHERE path=?", (key,)).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else None

    def put_cursor(self, history_id: str | None, watch_expiration_ms: str | None, *, advance: bool) -> dict:
        def op(docs: dict[str, dict | None]):
            current = dict(docs.get("control/cursor") or {})
            if watch_expiration_ms is not None:
                current["watch_expiration_ms"] = watch_expiration_ms
            if history_id is not None and (advance or not current.get("history_id")):
                previous = current.get("history_id")
                if previous and previous.isdigit() and history_id.isdigit() and int(history_id) < int(previous):
                    pass
                else:
                    current["history_id"] = history_id
            return {"control/cursor": current}, current

        return self._run(["control/cursor"], op)

    def message(self, message_id: str) -> dict | None:
        return self.get(f"msg:{message_id}")

    def latch(self) -> dict | None:
        return self.get("control/latch")

    def cursor(self) -> dict | None:
        return self.get("control/cursor")

    def _run(self, keys: list[str], fn) -> dict:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            docs: dict[str, dict | None] = {}
            for key in keys:
                row = conn.execute("SELECT body FROM docs WHERE path=?", (key,)).fetchone()
                docs[key] = json.loads(row[0]) if row else None
            updates, result = fn(docs)
            for key, value in updates.items():
                if value is None:
                    conn.execute("DELETE FROM docs WHERE path=?", (key,))
                else:
                    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
                    conn.execute(
                        "INSERT INTO docs(path, body) VALUES(?, ?) "
                        "ON CONFLICT(path) DO UPDATE SET body=excluded.body",
                        (key, payload),
                    )
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn


def _reserve_keys(message_id: str, thread_id: str, sender_key: str, now: datetime) -> list[str]:
    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return [
        f"msg:{message_id}",
        f"thread:{thread_id}",
        f"sender:{sender_key}",
        f"counter:{day}",
        "control/latch",
        "control/inflight",
    ]
