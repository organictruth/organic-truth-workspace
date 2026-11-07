"""Transactional acknowledgement state machine.

Exactly-once is not claimed. A row that has reached send_attempted is never
submitted again, including when the process dies before the provider result
is stored. Reserved rows have not been submitted and may still be sent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mail_ack.constants import ATTEMPT_STALE_SECONDS, DAILY_CAP, SENDER_WINDOW_SECONDS


def reserve(
    docs: dict[str, dict | None],
    *,
    message_id: str,
    thread_id: str,
    sender_key: str,
    sending_allowed: bool,
    now: datetime,
) -> tuple[dict[str, dict | None], dict]:
    msg_key = f"msg:{message_id}"
    if docs.get(msg_key):
        return {}, {"code": "duplicate", "state": docs[msg_key].get("state")}
    if not sending_allowed:
        return (
            {msg_key: _message("held_switch_off", "sending_disabled", message_id, thread_id, sender_key, now)},
            {"code": "held_switch_off"},
        )
    thread_key = f"thread:{thread_id}"
    if docs.get(thread_key):
        return (
            {msg_key: _message("suppressed_thread", "thread", message_id, thread_id, sender_key, now)},
            {"code": "suppressed_thread"},
        )
    sender_doc_key = f"sender:{sender_key}"
    sender_doc = docs.get(sender_doc_key)
    if sender_doc and _within_window(sender_doc.get("at"), now):
        return (
            {
                msg_key: _message(
                    "suppressed_sender_window", "sender_window", message_id, thread_id, sender_key, now
                )
            },
            {"code": "suppressed_sender_window"},
        )
    day = _day(now)
    counter = int((docs.get(f"counter:{day}") or {}).get("attempted") or 0)
    latch = docs.get("control/latch")
    if latch or counter >= DAILY_CAP:
        updates: dict[str, dict | None] = {
            msg_key: _message("suppressed_cap", "daily_cap", message_id, thread_id, sender_key, now)
        }
        if not latch:
            updates["control/latch"] = _latch(now, counter, day)
        return updates, {"code": "suppressed_cap", "latched": True}
    inflight = _ids(docs.get("control/inflight"))
    if message_id not in inflight:
        inflight.append(message_id)
    if len(inflight) > 100:
        return (
            {
                msg_key: _message("suppressed_cap", "inflight_overflow", message_id, thread_id, sender_key, now),
                "control/latch": _latch(now, counter, day, reason="inflight_overflow"),
            },
            {"code": "suppressed_cap", "latched": True},
        )
    return (
        {
            msg_key: _message("reserved", "reserved", message_id, thread_id, sender_key, now),
            thread_key: {"message_id": message_id, "at": _iso(now)},
            sender_doc_key: {"message_id": message_id, "at": _iso(now)},
            "control/inflight": {"ids": inflight},
        },
        {"code": "reserved"},
    )


def mark_attempted(
    docs: dict[str, dict | None], *, message_id: str, now: datetime
) -> tuple[dict[str, dict | None], dict]:
    msg_key = f"msg:{message_id}"
    message = docs.get(msg_key)
    if not message or message.get("state") != "reserved":
        return {}, {"ok": False, "code": "not_reserved"}
    day = _day(now)
    counter_key = f"counter:{day}"
    attempted = int((docs.get(counter_key) or {}).get("attempted") or 0)
    latch = docs.get("control/latch")
    if latch or attempted >= DAILY_CAP:
        updated = dict(message)
        updated["state"] = "suppressed_cap"
        updated["reason"] = "daily_cap"
        updated["updated_at"] = _iso(now)
        updates: dict[str, dict | None] = {
            msg_key: updated,
            "control/inflight": {"ids": [item for item in _ids(docs.get("control/inflight")) if item != message_id]},
        }
        if not latch:
            updates["control/latch"] = _latch(now, attempted, day)
        return updates, {"ok": False, "code": "suppressed_cap", "latched": True}
    attempted += 1
    updated = dict(message)
    updated["state"] = "send_attempted"
    updated["reason"] = "send_attempted"
    updated["updated_at"] = _iso(now)
    updates = {msg_key: updated, counter_key: {"attempted": attempted}}
    latched = attempted >= DAILY_CAP
    if latched:
        updates["control/latch"] = _latch(now, attempted, day)
    return updates, {"ok": True, "code": "send_attempted", "attempted": attempted, "latched": latched}


def finalize(
    docs: dict[str, dict | None], *, message_id: str, state: str, now: datetime
) -> tuple[dict[str, dict | None], dict]:
    if state not in {"sent", "send_uncertain", "send_rejected", "not_sent_recheck_failed"}:
        raise ValueError("bad_final_state")
    msg_key = f"msg:{message_id}"
    message = docs.get(msg_key)
    allowed_from = {"send_attempted"} if state != "not_sent_recheck_failed" else {"reserved", "send_attempted"}
    if not message or message.get("state") not in allowed_from:
        return {}, {"ok": False}
    if state != "not_sent_recheck_failed" and message.get("state") != "send_attempted":
        return {}, {"ok": False}
    updated = dict(message)
    updated["state"] = state
    updated["reason"] = state
    updated["updated_at"] = _iso(now)
    return (
        {
            msg_key: updated,
            "control/inflight": {"ids": [item for item in _ids(docs.get("control/inflight")) if item != message_id]},
        },
        {"ok": True},
    )


def age_attempt(
    docs: dict[str, dict | None], *, message_id: str, now: datetime
) -> tuple[dict[str, dict | None], dict]:
    msg_key = f"msg:{message_id}"
    message = docs.get(msg_key)
    if not message or message.get("state") != "send_attempted":
        return {}, {"ok": False}
    updated_at = datetime.fromisoformat(message["updated_at"])
    if now.astimezone(timezone.utc) - updated_at < timedelta(seconds=ATTEMPT_STALE_SECONDS):
        return {}, {"ok": False, "code": "in_progress"}
    return finalize(docs, message_id=message_id, state="send_uncertain", now=now)


def record_terminal(
    docs: dict[str, dict | None],
    *,
    message_id: str,
    thread_id: str,
    sender_key: str,
    state: str,
    reason: str,
    now: datetime,
) -> tuple[dict[str, dict | None], dict]:
    msg_key = f"msg:{message_id}"
    if docs.get(msg_key):
        return {}, {"code": "duplicate", "state": docs[msg_key].get("state")}
    if state not in {"ineligible"}:
        raise ValueError("bad_terminal")
    return (
        {msg_key: _message(state, reason, message_id, thread_id, sender_key, now)},
        {"code": "ineligible"},
    )


def claim_pubsub(
    docs: dict[str, dict | None], *, pubsub_id: str, now: datetime
) -> tuple[dict[str, dict | None], dict]:
    key = f"pubsub:{pubsub_id}"
    if docs.get(key):
        return {}, {"ok": False}
    if not pubsub_id or "/" in pubsub_id:
        return {}, {"ok": False}
    return {key: {"at": _iso(now)}}, {"ok": True}


def clear_latch(docs: dict[str, dict | None]) -> tuple[dict[str, dict | None], dict]:
    del docs
    return {"control/latch": None}, {"ok": True}


def _message(state: str, reason: str, message_id: str, thread_id: str, sender_key: str, now: datetime) -> dict:
    del message_id
    stamp = _iso(now)
    return {
        "state": state,
        "reason": reason,
        "thread_id": thread_id,
        "sender_key": sender_key,
        "created_at": stamp,
        "updated_at": stamp,
    }


def _latch(now: datetime, attempted: int, day: str, reason: str = "daily_cap") -> dict:
    return {
        "latched": True,
        "reason": reason,
        "at": _iso(now),
        "attempted": attempted,
        "day": day,
        "alert": "status_record_only",
    }


def _as_utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _within_window(stamp: str | None, now: datetime) -> bool:
    if not stamp:
        return False
    previous = datetime.fromisoformat(stamp)
    return _as_utc(now) - previous < timedelta(seconds=SENDER_WINDOW_SECONDS)


def _ids(doc: dict | None) -> list[str]:
    if not doc:
        return []
    return list(doc.get("ids") or [])


def _day(now: datetime) -> str:
    return _as_utc(now).strftime("%Y-%m-%d")


def _iso(now: datetime) -> str:
    return _as_utc(now).isoformat()
