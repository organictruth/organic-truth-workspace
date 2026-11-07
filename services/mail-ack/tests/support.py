"""Synthetic fixtures. These are not copies of customer mail or published headers."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from mail_ack.constants import BUSINESS_MAILBOX
from mail_ack.eligibility import FetchProof
from mail_ack.headers import Header
from mail_ack.arcverify import signing_input

NOW = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
SELECTOR = "synthetic-selector"


def generate_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def proof(message_id="m-1", thread_id="t-1", labels=("INBOX",)):
    return FetchProof(
        source="gmail_api",
        mailbox=BUSINESS_MAILBOX,
        message_id=message_id,
        thread_id=thread_id,
        label_ids=None if labels is None else tuple(labels),
        history_id="10",
    )


def inquiry(
    private_key,
    *,
    delivered_to=BUSINESS_MAILBOX,
    from_header="Buyer <buyer@buyer.example>",
    to_header=None,
    subject="Question about an order",
    body="Hello, I have a question about an order.\r\n",
    dmarc="pass",
    dkim="pass",
    spf="pass",
    header_from="buyer.example",
    return_path="<buyer@buyer.example>",
    omit_return_path=False,
    extra_before=(),
    extra_after=(),
    omit_arc=False,
    tamper=False,
    seal_cv="none",
    seal_i="1",
    seal_d="google.com",
    selector=SELECTOR,
    authserv="mx.google.com",
    received="from mail.buyer.example by mx.google.com with ESMTPS id synthetic",
    omit_received=False,
    labels_note=None,
):
    del labels_note
    rows = [("Delivered-To", delivered_to)]
    rows.extend(extra_before)
    if not omit_received:
        rows.append(("Received", received))
    aar = (
        f"i={seal_i}; {authserv}; dkim={dkim}; spf={spf}; dmarc={dmarc}; "
        f"header.from={header_from}"
    )
    ams = f"i={seal_i}; a=rsa-sha256; b=Zg=="
    seal = (
        f"i={seal_i}; a=rsa-sha256; cv={seal_cv}; d={seal_d}; s={selector}; "
        f"t={int(NOW.timestamp())}; b=QQ=="
    )
    if not omit_arc:
        rows.append(("ARC-Seal", seal))
        rows.append(("ARC-Message-Signature", ams))
        rows.append(("ARC-Authentication-Results", aar))
    if not omit_return_path:
        rows.append(("Return-Path", return_path))
    rows.extend(extra_after)
    rows.append(("From", from_header))
    rows.append(("To", BUSINESS_MAILBOX if to_header is None else to_header))
    rows.append(("Subject", subject))
    rows.append(("Message-ID", "<inquiry@buyer.example>"))
    rows.append(("Date", "Sat, 07 Nov 2026 15:00:00 +0000"))
    rows.append(("Content-Type", "text/plain; charset=utf-8"))
    if not omit_arc and private_key is not None:
        parsed = tuple(Header(name, value) for name, value in rows)
        payload = signing_input(parsed, int(seal_i))
        signature = private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        encoded = base64.b64encode(signature).decode("ascii")
        if tamper:
            encoded = ("A" if encoded[0] != "A" else "B") + encoded[1:]
        rows = [
            (name, value.replace("b=QQ==", f"b={encoded}") if name == "ARC-Seal" else value)
            for name, value in rows
        ]
    lines = [f"{name}: {value}" for name, value in rows]
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode("utf-8")
