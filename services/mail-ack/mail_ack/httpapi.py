"""HTTP entrypoints for authenticated Pub/Sub push and the daily task."""

from __future__ import annotations

from datetime import datetime, timezone
import base64
import json

from mail_ack.config import Config
from mail_ack.constants import SAME_MAILBOX_IDENTITIES
from mail_ack.store import Store
from mail_ack.worker import on_notification, renew_watch, reconcile


def handle_request(
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    *,
    config: Config,
    store: Store,
    client,
    keys: dict,
    verify,
    now: datetime | None = None,
) -> tuple[int, dict]:
    current = now or datetime.now(timezone.utc)
    if method == "GET" and path == "/health":
        return 200, {"ok": True, "sending_configured": config.sending_configured()}
    if method != "POST" or path not in {"/push", "/tasks/daily"}:
        return 404, {"error": "not_found"}
    email = verify(_header(headers, "authorization"))
    if not email:
        return 401, {"error": "unauthorized"}
    if client is None:
        return 503, {"error": "gmail_not_configured"}
    if path == "/tasks/daily":
        watch = renew_watch(client, store, config.pubsub_topic)
        caught = reconcile(client, store, config, keys, current)
        return 200, {"watch": watch, "reconcile": caught}
    notice = _push_notice(body)
    if notice is None:
        return 204, {"action": "ignored_malformed"}
    if notice["email"].lower() not in SAME_MAILBOX_IDENTITIES:
        return 204, {"action": "ignored_account"}
    claimed = store.claim_pubsub(notice["pubsub_id"], current)
    if not claimed.get("ok"):
        return 204, {"action": "duplicate_push"}
    result = on_notification(
        email_address=notice["email"],
        history_id=notice["history_id"],
        client=client,
        store=store,
        config=config,
        keys=keys,
        now=current,
    )
    return 204, result


def _push_notice(body: bytes) -> dict | None:
    try:
        envelope = json.loads(body.decode("utf-8"))
        message = envelope["message"]
        encoded = message["data"]
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        email = str(payload["emailAddress"])
        history_id = str(payload["historyId"])
        pubsub_id = str(message["messageId"])
    except Exception:
        return None
    if not email or not history_id or not pubsub_id or "/" in pubsub_id:
        return None
    return {"email": email, "history_id": history_id, "pubsub_id": pubsub_id}


def _header(headers: dict[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None
