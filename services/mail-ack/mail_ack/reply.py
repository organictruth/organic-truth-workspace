"""Build the fixed acknowledgement. Reply-To is never a recipient."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
import re
import uuid

from mail_ack.addresses import is_denied_recipient, parse_single_mailbox
from mail_ack.constants import ACK_TEXT, AUTO_ACK_HEADER, AUTO_ACK_VALUE, BUSINESS_MAILBOX
from mail_ack.eligibility import EligibilityPermit, PermitError
from mail_ack.headers import all_values, parse_message


class ReplyError(Exception):
    pass


def build_reply(permit: EligibilityPermit, now: datetime) -> bytes:
    if type(permit) is not EligibilityPermit:
        raise PermitError("untrusted_permit")
    permit.authenticated()
    if is_denied_recipient(permit.sender):
        raise ReplyError("denied_recipient")
    message = EmailMessage()
    message["From"] = f"Organic Truth <{BUSINESS_MAILBOX}>"
    message["To"] = permit.sender
    message["Subject"] = _subject(permit.subject)
    message["Date"] = format_datetime(now.astimezone(timezone.utc))
    message["Message-ID"] = f"<{uuid.uuid4().hex}@organictruthllc.com>"
    message["Auto-Submitted"] = "auto-replied"
    message["X-Auto-Response-Suppress"] = "All"
    message["Precedence"] = "auto_reply"
    message[AUTO_ACK_HEADER] = AUTO_ACK_VALUE
    message["MIME-Version"] = "1.0"
    if permit.message_id_header:
        message["In-Reply-To"] = permit.message_id_header
        message["References"] = permit.message_id_header
    message.set_content(ACK_TEXT, subtype="plain", charset="utf-8", cte="7bit")
    raw = message.as_bytes()
    validate_outbound(raw, permit.sender)
    return raw


def validate_outbound(raw: bytes, expected_sender: str) -> None:
    headers, body = parse_message(raw)
    forbidden = {"cc", "bcc", "reply-to", "list-unsubscribe", "list-unsubscribe-post"}
    present = {header.lower_name for header in headers}
    if present & forbidden:
        raise ReplyError("forbidden_header")
    recipients = all_values(headers, "to")
    if len(recipients) != 1:
        raise ReplyError("recipient_count")
    mailbox = parse_single_mailbox(recipients[0])
    if mailbox.address != expected_sender or is_denied_recipient(mailbox.address):
        raise ReplyError("recipient")
    sender = parse_single_mailbox(all_values(headers, "from")[0])
    if sender.address != BUSINESS_MAILBOX:
        raise ReplyError("from")
    if all_values(headers, "auto-submitted") != ("auto-replied",):
        raise ReplyError("auto_submitted")
    if all_values(headers, AUTO_ACK_HEADER) != (AUTO_ACK_VALUE,):
        raise ReplyError("loop_header")
    text = body.decode("utf-8")
    if ACK_TEXT not in text:
        raise ReplyError("body")
    extra = text.replace(ACK_TEXT, "").strip()
    if extra:
        raise ReplyError("body_extra")
    if "unsubscribe" in text.lower():
        raise ReplyError("unsubscribe")


def _subject(original: str) -> str:
    cleaned = re.sub(r"[\r\n\x00]+", " ", original).strip()
    if not cleaned:
        base = "Re: your message"
    elif cleaned.lower().startswith("re:"):
        base = cleaned
    else:
        base = f"Re: {cleaned}"
    return base[:180]
