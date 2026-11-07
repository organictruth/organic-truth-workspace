"""Process one fetched message. No path sends without a permit and a reservation."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from mail_ack.config import Config, effective_sending
from mail_ack.eligibility import Decision, evaluate
from mail_ack.reply import ReplyError, build_reply
from mail_ack.statusfile import write_status
from mail_ack.store import Store

log = logging.getLogger("mail_ack")


class SendResult:
    def __init__(self, kind: str, remote_id: str | None = None):
        self.kind = kind
        self.remote_id = remote_id


def handle_message(
    *,
    raw: bytes,
    proof,
    config: Config,
    store: Store,
    transport,
    keys: dict,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now(timezone.utc)
    existing = store.message(proof.message_id)
    if existing and existing.get("state") == "send_attempted":
        store.age_attempt(proof.message_id, current)
        log.info("message=%s outcome=send_not_retried", proof.message_id)
        return {"outcome": "send_not_retried", "reason": "send_uncertain"}
    if existing and existing.get("state") == "reserved":
        return _resume(raw, proof, existing, config, store, transport, keys, current)
    if existing:
        return {"outcome": "duplicate", "reason": existing.get("state", "duplicate")}

    decision = evaluate(raw, proof, keys, now=current.timestamp())
    if not decision.eligible or decision.permit is None:
        store.record_ineligible(
            message_id=proof.message_id,
            thread_id=proof.thread_id,
            sender_key="",
            reason=decision.reason,
            now=current,
        )
        log.info("message=%s outcome=ineligible reason=%s", proof.message_id, decision.reason)
        return {"outcome": "ineligible", "reason": decision.reason}

    allowed = effective_sending(config, store.latch())
    reserved = store.reserve(
        message_id=proof.message_id,
        thread_id=proof.thread_id,
        sender_key=decision.permit.sender_key,
        sending_allowed=allowed,
        now=current,
    )
    _record_latch(config, store, reserved)
    if reserved.get("code") != "reserved":
        log.info("message=%s outcome=%s", proof.message_id, reserved.get("code"))
        return {"outcome": reserved.get("code"), "reason": reserved.get("code")}
    return _submit(decision, config, store, transport, current)


def _resume(raw, proof, existing, config, store, transport, keys, now) -> dict:
    decision = evaluate(raw, proof, keys, now=now.timestamp())
    if (
        not decision.eligible
        or decision.permit is None
        or decision.permit.sender_key != existing.get("sender_key")
    ):
        store.finalize(proof.message_id, "not_sent_recheck_failed", now)
        return {"outcome": "not_sent_recheck_failed", "reason": "recheck_failed"}
    if not effective_sending(config, store.latch()):
        return {"outcome": "held_switch_off", "reason": "sending_disabled"}
    return _submit(decision, config, store, transport, now)


def _submit(decision: Decision, config: Config, store: Store, transport, now: datetime) -> dict:
    permit = decision.permit
    assert permit is not None
    if not effective_sending(config, store.latch()):
        return {"outcome": "held_switch_off", "reason": "sending_disabled"}
    try:
        raw = build_reply(permit, now)
    except (ReplyError, Exception):
        store.finalize(permit.message_id, "not_sent_recheck_failed", now)
        log.info("message=%s outcome=not_sent_invalid", permit.message_id)
        return {"outcome": "not_sent_invalid", "reason": "outbound_invalid"}
    attempted = store.mark_attempted(permit.message_id, now)
    _record_latch(config, store, attempted)
    if not attempted.get("ok"):
        log.info("message=%s outcome=%s", permit.message_id, attempted.get("code"))
        return {"outcome": attempted.get("code"), "reason": attempted.get("code")}
    fresh = store.message(permit.message_id)
    if not fresh or fresh.get("state") != "send_attempted":
        return {"outcome": "send_not_retried", "reason": "state_moved"}
    if not config.sending_configured():
        store.finalize(permit.message_id, "send_uncertain", now)
        return {"outcome": "send_uncertain", "reason": "switch_after_attempt"}
    try:
        result = transport.send(raw)
        kind = result.kind
    except Exception:
        kind = "uncertain"
    if kind == "accepted":
        store.finalize(permit.message_id, "sent", now)
        log.info("message=%s outcome=sent", permit.message_id)
        return {"outcome": "sent", "reason": "sent"}
    if kind == "rejected":
        store.finalize(permit.message_id, "send_rejected", now)
        log.info("message=%s outcome=send_rejected", permit.message_id)
        return {"outcome": "send_rejected", "reason": "send_rejected"}
    store.finalize(permit.message_id, "send_uncertain", now)
    log.info("message=%s outcome=send_uncertain", permit.message_id)
    return {"outcome": "send_uncertain", "reason": "send_uncertain"}


def _record_latch(config: Config, store: Store, result: dict) -> None:
    if not result.get("latched"):
        return
    latch = store.latch() or {}
    write_status(
        config.status_path,
        {
            "sending_latched_off": True,
            "latch_reason": latch.get("reason", "daily_cap"),
            "attempted_on_utc_day": latch.get("attempted"),
            "day": latch.get("day"),
        },
    )
