"""Watch renewal and history catch-up. Push is a wake-up, not a latency promise."""

from __future__ import annotations

from datetime import datetime, timezone

from mail_ack.config import Config
from mail_ack.constants import HISTORY_PAGE_CAP, RECONCILE_MESSAGE_CAP, SAME_MAILBOX_IDENTITIES
from mail_ack.gmail import GmailClient, HistoryExpired
from mail_ack.pipeline import handle_message
from mail_ack.statusfile import write_status
from mail_ack.store import Store


def baseline_if_needed(client: GmailClient, store: Store) -> bool:
    """Record the current history id and do not read older mail."""
    if store.cursor() and store.cursor().get("history_id"):
        return False
    store.put_cursor(client.profile_history_id(), None, advance=False)
    return True


def renew_watch(client: GmailClient, store: Store, topic: str) -> dict:
    if not topic:
        return {"action": "watch_not_configured"}
    watched = client.watch(topic)
    # A watch response must not skip history that has not been listed yet.
    store.put_cursor(None, watched["expiration"], advance=False)
    if not (store.cursor() or {}).get("history_id"):
        store.put_cursor(watched["historyId"], watched["expiration"], advance=False)
        return {"action": "watch_baselined", "expiration": watched["expiration"]}
    return {"action": "watch_renewed", "expiration": watched["expiration"]}


def reconcile(client: GmailClient, store: Store, config: Config, keys: dict, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    cursor = store.cursor() or {}
    start = cursor.get("history_id")
    if not start:
        baseline_if_needed(client, store)
        return {"action": "baseline", "processed": 0}
    try:
        message_ids, newest, truncated = _added_since(client, start)
    except HistoryExpired:
        return _bounded(client, store, config, keys, current)
    processed = 0
    for message_id in message_ids:
        fetched = client.get_message(message_id)
        handle_message(
            raw=fetched.raw,
            proof=fetched.proof,
            config=config,
            store=store,
            transport=client,
            keys=keys,
            now=current,
        )
        processed += 1
    if newest:
        store.put_cursor(newest, None, advance=True)
    if truncated:
        write_status(config.status_path, {"history_truncated": True, "processed": processed})
    return {"action": "history", "processed": processed, "truncated": truncated}


def on_notification(
    *,
    email_address: str,
    history_id: str,
    client: GmailClient,
    store: Store,
    config: Config,
    keys: dict,
    now: datetime | None = None,
) -> dict:
    """Wake on a Gmail push. The push address is not delivery proof."""
    if email_address.lower() not in SAME_MAILBOX_IDENTITIES:
        return {"action": "ignored_account"}
    if baseline_if_needed(client, store):
        return {"action": "baseline", "history_id": history_id}
    return reconcile(client, store, config, keys, now)


def _added_since(client: GmailClient, start: str) -> tuple[list[str], str | None, bool]:
    message_ids: list[str] = []
    page_token = None
    newest = None
    pages = 0
    while pages < HISTORY_PAGE_CAP:
        page = client.history_page(start, page_token)
        for record in page.get("history") or []:
            newest = str(record.get("id") or newest or "")
            for added in record.get("messagesAdded") or []:
                message = (added or {}).get("message") or {}
                if message.get("id"):
                    message_ids.append(message["id"])
        page_token = page.get("nextPageToken")
        pages += 1
        if not page_token:
            newest = str(page.get("historyId") or newest or "")
            return message_ids, newest or None, False
    return message_ids, newest or None, True


def _bounded(client, store, config, keys, now) -> dict:
    ids = client.list_recent_ids(RECONCILE_MESSAGE_CAP)
    processed = 0
    for message_id in ids:
        if store.message(message_id):
            continue
        fetched = client.get_message(message_id)
        handle_message(
            raw=fetched.raw,
            proof=fetched.proof,
            config=config,
            store=store,
            transport=client,
            keys=keys,
            now=now,
        )
        processed += 1
    store.put_cursor(client.profile_history_id(), None, advance=True)
    truncated = len(ids) >= RECONCILE_MESSAGE_CAP
    if truncated:
        write_status(config.status_path, {"reconcile_truncated": True, "processed": processed})
    return {"action": "reconcile_bounded", "processed": processed, "truncated": truncated}
