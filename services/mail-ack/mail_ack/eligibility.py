"""Eligibility gate. A permit can be minted only by evaluate()."""

from __future__ import annotations

from dataclasses import dataclass
import email
import email.policy
import re

from mail_ack.addresses import (
    AddressError,
    is_automated_sender,
    is_denied_recipient,
    is_own_domain,
    mentions_personal_alias,
    parse_address_list,
    parse_single_mailbox,
)
from mail_ack.arcverify import ArcError, VerifiedSeal, verify_direct_google_seal
from mail_ack.authparse import AuthParseError, parse_authentication_results, strip_comments
from mail_ack.constants import (
    AUTO_ACK_HEADER,
    BUSINESS_MAILBOX,
    GOOGLE_AUTHSERV_ID,
    GOOGLE_MX_HOST,
    PERSONAL_ALIAS,
    PERSONAL_ALIAS_GOOGLEMAIL,
    SAME_MAILBOX_IDENTITIES,
)
from mail_ack.headers import Header, HeaderError, all_values, first_value, indexes, parse_message
from mail_ack.risk import risk_category
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


_PERMIT_TOKEN = object()

_BULK_NAMES = frozenset(
    {
        "list-id",
        "list-unsubscribe",
        "list-unsubscribe-post",
        "list-post",
        "list-help",
        "mailing-list",
        "x-mailing-list",
        "feedback-id",
        "x-feedback-id",
        "resent-from",
        "resent-to",
        "resent-date",
        "x-forwarded-to",
        "x-forwarded-for",
        "x-auto-reply",
        "x-autoreply",
        "x-autorespond",
    }
)
_DENY_LABELS = frozenset(
    {
        "SPAM",
        "TRASH",
        "CATEGORY_PROMOTIONS",
        "CATEGORY_UPDATES",
        "CATEGORY_SOCIAL",
        "CATEGORY_FORUMS",
    }
)
_MAX_TEXT = 64 * 1024


class PermitError(Exception):
    pass


@dataclass(frozen=True)
class FetchProof:
    """In-process attestation that bytes came from the Gmail API.

    Header fields cannot create this object. A Delivered-To or
    Authentication-Results value is not a fetch proof.
    """

    source: str
    mailbox: str
    message_id: str
    thread_id: str
    label_ids: tuple[str, ...] | None
    history_id: str | None = None


@dataclass(frozen=True)
class EligibilityPermit:
    message_id: str
    thread_id: str
    sender: str
    sender_key: str
    subject: str
    message_id_header: str | None
    _token: object

    def __post_init__(self) -> None:
        if self._token is not _PERMIT_TOKEN:
            raise PermitError("untrusted_permit")

    def authenticated(self) -> None:
        if type(self) is not EligibilityPermit or self._token is not _PERMIT_TOKEN:
            raise PermitError("untrusted_permit")


@dataclass(frozen=True)
class Decision:
    eligible: bool
    reason: str
    permit: EligibilityPermit | None = None


def evaluate(
    raw: bytes,
    proof: FetchProof,
    keys: dict[str, RSAPublicKey],
    *,
    now: float | None = None,
) -> Decision:
    """Return a permit only when every positive check holds."""
    try:
        return _evaluate(raw, proof, keys, now=now)
    except PermitError:
        raise
    except (HeaderError, AddressError, AuthParseError, ArcError, ValueError):
        return Decision(False, "malformed_or_unproven")
    except Exception:
        return Decision(False, "evaluation_error")


def _evaluate(
    raw: bytes,
    proof: FetchProof,
    keys: dict[str, RSAPublicKey],
    *,
    now: float | None,
) -> Decision:
    if type(proof) is not FetchProof or proof.source != "gmail_api":
        return Decision(False, "unproven_fetch")
    if proof.mailbox.lower() not in SAME_MAILBOX_IDENTITIES:
        return Decision(False, "unproven_fetch")
    if not proof.message_id or not proof.thread_id or "/" in proof.message_id:
        return Decision(False, "unproven_fetch")
    if proof.label_ids is None:
        return Decision(False, "labels_unknown")
    if any(label in _DENY_LABELS for label in proof.label_ids):
        return Decision(False, "spam_or_bulk_label")

    headers, body = parse_message(raw)
    structural = _structural_reason(headers)
    if structural:
        return Decision(False, structural)

    if not keys:
        return Decision(False, "provenance_unverified")
    try:
        seal = verify_direct_google_seal(headers, keys, now=now)
    except ArcError:
        return Decision(False, "provenance_unverified")

    # Grant DMARC only from the seal-covered ARC-Authentication-Results value.
    # Standalone Authentication-Results headers are attacker-writable and ignored.
    verdict_reason = _sealed_verdict(seal)
    if verdict_reason:
        return Decision(False, verdict_reason)

    delivery = _delivery_reason(headers)
    if delivery:
        return Decision(False, delivery)
    return_path = _return_path_reason(headers)
    if return_path:
        return Decision(False, return_path)

    sender = _sender(headers)
    if isinstance(sender, str):
        return Decision(False, sender)
    if is_own_domain(sender) or is_denied_recipient(sender.address) or is_automated_sender(sender):
        return Decision(False, "sender_not_allowed")
    from_domain = _from_domain(seal)
    if from_domain != sender.domain:
        return Decision(False, "from_alignment")

    content_reason = _content_reason(raw, headers, body)
    if content_reason:
        return Decision(False, content_reason)

    subject = _safe_subject(first_value(headers, "subject") or "")
    message_id = _safe_message_id(first_value(headers, "message-id"))
    permit = EligibilityPermit(
        message_id=proof.message_id,
        thread_id=proof.thread_id,
        sender=sender.address,
        sender_key=sender.suppression_key,
        subject=subject,
        message_id_header=message_id,
        _token=_PERMIT_TOKEN,
    )
    return Decision(True, "eligible", permit)


def _structural_reason(headers: tuple[Header, ...]) -> str | None:
    names = {header.lower_name for header in headers}
    if names & _BULK_NAMES:
        return "bulk_or_forwarded_or_auto"
    auto = first_value(headers, "auto-submitted")
    if auto is not None and auto.lower() != "no":
        return "autoresponder"
    precedence = first_value(headers, "precedence")
    if precedence and precedence.lower() in {"bulk", "list", "junk", "auto_reply", "auto-reply"}:
        return "bulk_or_forwarded_or_auto"
    if any(header.lower_name == AUTO_ACK_HEADER.lower() for header in headers):
        return "loop_header"
    if first_value(headers, "x-auto-response-suppress"):
        return "autoresponder"
    content_type = (first_value(headers, "content-type") or "").lower()
    if "multipart/report" in content_type or "message/rfc822" in content_type:
        return "bounce_or_forwarded"
    if "multipart/encrypted" in content_type or "pkcs7-mime" in content_type:
        return "unsupported_content"
    spam_flag = first_value(headers, "x-spam-flag")
    if spam_flag and spam_flag.strip().lower() == "yes":
        return "spam_or_bulk_label"
    return None


def _sealed_verdict(seal: VerifiedSeal) -> str | None:
    try:
        verdict = parse_authentication_results(_aar_auth_portion(seal))
    except AuthParseError:
        return "provenance_unverified"
    if verdict.authserv_id != GOOGLE_AUTHSERV_ID:
        return "provenance_unverified"
    if verdict.methods.get("dmarc") != "pass":
        return "dmarc_not_pass"
    if verdict.methods.get("dkim") != "pass" and verdict.methods.get("spf") != "pass":
        return "dmarc_not_pass"
    aligned = verdict.properties.get("header.from", "").lower().rstrip(".")
    if not aligned:
        return "from_alignment"
    return None


def _aar_auth_portion(seal: VerifiedSeal) -> str:
    """Return the AAR value with the i= tag removed so authserv-id is first."""
    value = seal.authenticated_results_value.strip()
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if not parts:
        raise AuthParseError("missing_methods")
    if parts[0].lower().startswith("i="):
        parts = parts[1:]
    if not parts:
        raise AuthParseError("missing_methods")
    return "; ".join(parts)


def _from_domain(seal: VerifiedSeal) -> str | None:
    try:
        verdict = parse_authentication_results(_aar_auth_portion(seal))
    except AuthParseError:
        return None
    domain = verdict.properties.get("header.from", "").lower().rstrip(".")
    return domain or None


def _delivery_reason(headers: tuple[Header, ...]) -> str | None:
    seal_at = indexes(headers, "arc-seal")
    if len(seal_at) != 1:
        return "delivered_to_unproven"
    delivered = indexes(headers, "delivered-to")
    if len(delivered) != 1:
        return "delivered_to_ambiguous" if delivered else "delivered_to_unproven"
    if delivered[0] > seal_at[0]:
        return "delivered_to_unproven"
    try:
        mailbox = parse_single_mailbox(headers[delivered[0]].value)
    except AddressError:
        return "delivered_to_unproven"
    if mailbox.address in {PERSONAL_ALIAS, PERSONAL_ALIAS_GOOGLEMAIL}:
        return "delivered_to_personal_alias"
    if mailbox.address != BUSINESS_MAILBOX:
        return "delivered_to_not_business"
    to_values = all_values(headers, "to")
    if len(to_values) != 1:
        return "recipient_ambiguous"
    for name in ("to", "cc", "delivered-to", "x-original-to", "envelope-to"):
        for value in all_values(headers, name):
            if mentions_personal_alias(value):
                return "alias_ambiguous"
            if name not in {"to", "cc"}:
                continue
            try:
                mailboxes = parse_address_list(value)
            except AddressError:
                return "recipient_ambiguous"
            if name == "cc" and mailboxes:
                return "recipient_ambiguous"
            if name == "to" and (len(mailboxes) != 1 or mailboxes[0].address != BUSINESS_MAILBOX):
                return "recipient_ambiguous"
    if not _google_mx_before_from(headers):
        return "google_mx_unproven"
    return None


def _google_mx_before_from(headers: tuple[Header, ...]) -> bool:
    from_at = indexes(headers, "from")
    if len(from_at) != 1:
        return False
    for index, header in enumerate(headers):
        if index >= from_at[0]:
            break
        if header.lower_name != "received":
            continue
        if GOOGLE_MX_HOST in _received_by_hosts(header.value):
            return True
    return False


def _received_by_hosts(value: str) -> set[str]:
    try:
        text = re.sub(r"[ \t]+", " ", strip_comments(value))
    except AuthParseError:
        return set()
    tokens = text.replace(";", " ").split()
    hosts: set[str] = set()
    for index, token in enumerate(tokens):
        if token.lower() != "by" or index + 1 >= len(tokens):
            continue
        host = tokens[index + 1].strip().strip("[]").rstrip(".").lower()
        if re.fullmatch(r"[a-z0-9.-]{1,253}", host):
            hosts.add(host)
    return hosts


def _return_path_reason(headers: tuple[Header, ...]) -> str | None:
    from_at = indexes(headers, "from")
    if len(from_at) != 1:
        return "missing_return_path"
    paths = [headers[index].value for index in indexes(headers, "return-path") if index < from_at[0]]
    if not paths:
        return "missing_return_path"
    first = paths[0].strip()
    if first in {"<>", ""}:
        return "empty_return_path"
    try:
        parse_single_mailbox(first)
    except AddressError:
        return "malformed_return_path"
    return None


def _sender(headers: tuple[Header, ...]):
    values = all_values(headers, "from")
    if len(values) != 1:
        return "malformed_from"
    try:
        return parse_single_mailbox(values[0])
    except AddressError:
        return "malformed_from"


def _content_reason(raw: bytes, headers: tuple[Header, ...], body: bytes) -> str | None:
    del body
    subject = first_value(headers, "subject") or ""
    try:
        extracted = _extract_text(raw)
    except Exception:
        return "unclassified_content"
    if extracted is None:
        return "unsupported_content"
    if len(extracted) > _MAX_TEXT:
        return "unclassified_content"
    return risk_category(subject + "\n" + extracted)


def _extract_text(raw: bytes) -> str | None:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    chunks: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            if part.get_content_type() in {"multipart/report", "multipart/encrypted"}:
                return None
            continue
        ctype = part.get_content_type()
        if ctype in {"message/rfc822", "application/pkcs7-mime"}:
            return None
        if ctype.startswith("image/"):
            continue
        if not ctype.startswith("text/"):
            return None
        try:
            payload = part.get_content()
        except Exception:
            return None
        if not isinstance(payload, str):
            return None
        chunks.append(payload)
        if ctype == "text/html":
            encoded = part.get_payload(decode=True)
            if isinstance(encoded, bytes):
                chunks.append(encoded.decode("utf-8", errors="replace"))
    return "\n".join(chunks)


def _safe_subject(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned[:180]


def _safe_message_id(value: str | None) -> str | None:
    if value is None:
        return None
    if re.fullmatch(r"<[A-Za-z0-9._+=/@-]{1,200}>", value):
        return value
    return None

