"""Verify a direct (i=1, cv=none) ARC seal.

Authentication-Results text is not trusted unless this signature verifies
under the configured public key for the seal's selector. A matching
authserv-id string is not sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import re
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from mail_ack.constants import ARC_FUTURE_SKEW_SECONDS, ARC_MAX_AGE_SECONDS, GOOGLE_ARC_DOMAIN
from mail_ack.headers import Header


class ArcError(Exception):
    pass


@dataclass(frozen=True)
class VerifiedSeal:
    instance: int
    domain: str
    selector: str
    authenticated_results_value: str


def load_public_key(pem: str) -> RSAPublicKey:
    key = serialization.load_pem_public_key(pem.encode("ascii"))
    if not isinstance(key, RSAPublicKey):
        raise ArcError("not_rsa")
    return key


def signing_input(headers: tuple[Header, ...], instance: int) -> bytes:
    """RFC 8617 seal input: canon(AAR) + canon(AMS) + canon(seal with b= empty)."""
    aar = _one(headers, "arc-authentication-results", instance)
    ams = _one(headers, "arc-message-signature", instance)
    seal = _one(headers, "arc-seal", instance)
    seal_value = _empty_b_value(seal.value)
    return b"".join(
        [
            _relaxed("arc-authentication-results", aar.value),
            _relaxed("arc-message-signature", ams.value),
            _relaxed("arc-seal", seal_value),
        ]
    )


def verify_direct_google_seal(
    headers: tuple[Header, ...],
    keys: dict[str, RSAPublicKey],
    *,
    now: float | None = None,
) -> VerifiedSeal:
    """Verify exactly one i=1 cv=none d=google.com seal. Otherwise raise ArcError."""
    seals = [header for header in headers if header.lower_name == "arc-seal"]
    if len(seals) != 1:
        raise ArcError("seal_count")
    tags = _tags(seals[0].value)
    if tags.get("i") != "1" or tags.get("cv") != "none":
        raise ArcError("not_direct")
    if tags.get("a") != "rsa-sha256":
        raise ArcError("algorithm")
    if tags.get("d") != GOOGLE_ARC_DOMAIN:
        raise ArcError("domain")
    selector = tags.get("s") or ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", selector):
        raise ArcError("selector")
    timestamp = tags.get("t") or ""
    if not re.fullmatch(r"[0-9]{1,12}", timestamp):
        raise ArcError("timestamp")
    issued = int(timestamp)
    current = time.time() if now is None else now
    if issued > current + ARC_FUTURE_SKEW_SECONDS or current - issued > ARC_MAX_AGE_SECONDS:
        raise ArcError("timestamp_window")
    signature_b64 = tags.get("b") or ""
    if not signature_b64:
        raise ArcError("missing_signature")
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception as exc:
        raise ArcError("signature_encoding") from exc
    public_key = keys.get(selector)
    if public_key is None:
        raise ArcError("unpinned_selector")
    try:
        public_key.verify(signature, signing_input(headers, 1), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise ArcError("bad_signature") from exc
    aar = _one(headers, "arc-authentication-results", 1)
    _one(headers, "arc-message-signature", 1)
    return VerifiedSeal(
        instance=1,
        domain=GOOGLE_ARC_DOMAIN,
        selector=selector,
        authenticated_results_value=aar.value,
    )


def _one(headers: tuple[Header, ...], name: str, instance: int) -> Header:
    found = []
    for header in headers:
        if header.lower_name != name:
            continue
        if _instance(header.value) == str(instance):
            found.append(header)
    if len(found) != 1:
        raise ArcError(f"{name}_count")
    return found[0]


def _instance(value: str) -> str | None:
    found = None
    for part in value.split(";"):
        piece = part.strip()
        if "=" not in piece:
            continue
        name, raw = piece.split("=", 1)
        if name.strip().lower() != "i":
            continue
        if found is not None:
            raise ArcError("duplicate_instance")
        found = raw.strip()
    return found


def _tags(value: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in value.split(";"):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ArcError("tag")
        name, raw_val = piece.split("=", 1)
        name = name.strip().lower()
        if not re.fullmatch(r"[a-z0-9]{1,32}", name):
            raise ArcError("tag_name")
        if name in tags:
            raise ArcError("duplicate_tag")
        tags[name] = raw_val.strip()
    if not tags:
        raise ArcError("no_tags")
    return tags


def _empty_b_value(value: str) -> str:
    tags = _tags(value)
    if "b" not in tags:
        raise ArcError("missing_signature")
    rebuilt: list[str] = []
    # Preserve tag order from the header.
    for part in value.split(";"):
        piece = part.strip()
        if not piece:
            continue
        name = piece.split("=", 1)[0].strip().lower()
        if name == "b":
            rebuilt.append("b=")
        else:
            raw_val = tags[name]
            rebuilt.append(f"{name}={raw_val}")
    return "; ".join(rebuilt)


def _relaxed(name: str, value: str) -> bytes:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", name):
        raise ArcError("canon_name")
    collapsed = re.sub(r"[ \t]+", " ", value).strip(" ")
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in collapsed):
        raise ArcError("canon_range")
    line = f"{name}:{collapsed}\r\n"
    return line.encode("ascii")
