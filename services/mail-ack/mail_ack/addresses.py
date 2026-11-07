"""Strict mailbox parsing. Display names and extra addresses fail closed."""

from __future__ import annotations

from dataclasses import dataclass
import re

from mail_ack.authparse import strip_comments, AuthParseError
from mail_ack.constants import (
    DENIED_RECIPIENTS,
    OWN_DOMAIN,
    PERSONAL_ALIAS,
    PERSONAL_ALIAS_GOOGLEMAIL,
)


class AddressError(Exception):
    pass


@dataclass(frozen=True)
class Mailbox:
    address: str
    local: str
    domain: str

    @property
    def suppression_key(self) -> str:
        local = self.local.split("+", 1)[0].lower()
        domain = self.domain.lower()
        if domain in {"gmail.com", "googlemail.com"}:
            local = local.replace(".", "")
        return f"{local}@{domain}"


_LOCAL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?")
_DOMAIN_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)


def parse_single_mailbox(value: str) -> Mailbox:
    if value is None or any(ch in value for ch in ("\r", "\n", "\x00")):
        raise AddressError("malformed")
    try:
        text = strip_comments(value).strip()
    except AuthParseError as exc:
        raise AddressError("malformed") from exc
    if not text or "@" not in text:
        raise AddressError("malformed")
    if text.count("<") > 1 or text.count(">") > 1:
        raise AddressError("multiple")
    if "<" in text or ">" in text:
        if not (text.endswith(">") and text.count("<") == 1):
            raise AddressError("malformed")
        display, addr = text[:-1].split("<", 1)
        if "@" in display:
            raise AddressError("multiple")
        text = addr.strip()
    if "," in text or ";" in text or " " in text:
        raise AddressError("multiple")
    if text.count("@") != 1:
        raise AddressError("malformed")
    local, domain = text.split("@", 1)
    domain = domain.lower().rstrip(".")
    if domain.startswith("xn--") or any(ord(ch) > 126 for ch in local + domain):
        raise AddressError("unsupported_idna")
    if not _LOCAL_RE.fullmatch(local) or not _DOMAIN_RE.fullmatch(domain):
        raise AddressError("malformed")
    if ".." in local or local.startswith(".") or local.endswith("."):
        raise AddressError("malformed")
    return Mailbox(address=f"{local.lower()}@{domain}", local=local, domain=domain)


def parse_address_list(value: str) -> tuple[Mailbox, ...]:
    if value is None or any(ch in value for ch in ("\r", "\n", "\x00")):
        raise AddressError("malformed")
    try:
        text = strip_comments(value).strip()
    except AuthParseError as exc:
        raise AddressError("malformed") from exc
    if not text:
        return ()
    parts = re.split(r"[,;]", text)
    return tuple(parse_single_mailbox(part.strip()) for part in parts if part.strip())


def is_own_domain(mailbox: Mailbox) -> bool:
    return mailbox.domain == OWN_DOMAIN or mailbox.domain.endswith("." + OWN_DOMAIN)


def is_denied_recipient(address: str) -> bool:
    try:
        mailbox = parse_single_mailbox(address)
    except AddressError:
        return True
    if mailbox.address in DENIED_RECIPIENTS:
        return True
    if is_own_domain(mailbox):
        return True
    return False


def mentions_personal_alias(value: str) -> bool:
    lowered = value.lower()
    return PERSONAL_ALIAS in lowered or PERSONAL_ALIAS_GOOGLEMAIL in lowered


_AUTOMATED_LOCALS = frozenset(
    {
        "noreply",
        "no-reply",
        "no_reply",
        "donotreply",
        "do-not-reply",
        "do_not_reply",
        "mailer-daemon",
        "mailerdaemon",
        "postmaster",
        "bounce",
        "bounces",
        "notification",
        "notifications",
        "notify",
        "daemon",
    }
)


def is_automated_sender(mailbox: Mailbox) -> bool:
    base = mailbox.local.split("+", 1)[0].lower().replace(".", "")
    compact = {item.replace("-", "").replace("_", "") for item in _AUTOMATED_LOCALS}
    if base in compact or mailbox.local.split("+", 1)[0].lower() in _AUTOMATED_LOCALS:
        return True
    return "noreply" in base or "donotreply" in base or "mailerdaemon" in base
