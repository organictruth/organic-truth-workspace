"""Verify Google OIDC tokens for Pub/Sub and Scheduler. Missing keys fail closed."""

from __future__ import annotations

import base64
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers
from cryptography.exceptions import InvalidSignature


def verify_oidc(
    authorization: str | None,
    *,
    keys_by_kid: dict[str, RSAPublicKey],
    audience: str,
    allowed_emails: set[str],
    now: float | None = None,
) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    if not keys_by_kid or not audience or not allowed_emails:
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        signing_input, header, payload, signature = _split(token)
        if header.get("alg") != "RS256":
            return None
        public_key = keys_by_kid.get(header.get("kid", ""))
        if public_key is None:
            return None
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, KeyError):
        return None
    current = time.time() if now is None else now
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= current:
        return None
    if payload.get("iss") not in {"https://accounts.google.com", "accounts.google.com"}:
        return None
    aud = payload.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if audience not in audiences:
        return None
    email = payload.get("email")
    if payload.get("email_verified") is not True or not isinstance(email, str):
        return None
    if email.lower() not in {item.lower() for item in allowed_emails}:
        return None
    return email.lower()


def fetch_jwks(url: str) -> dict[str, RSAPublicKey]:
    if not url.startswith("https://"):
        raise ValueError("jwks_url")
    from urllib.request import urlopen

    with urlopen(url, timeout=5) as response:
        document = json.loads(response.read().decode("utf-8"))
    return keys_from_jwks(document)


def keys_from_jwks(document: dict) -> dict[str, RSAPublicKey]:
    keys: dict[str, RSAPublicKey] = {}
    for item in document.get("keys") or []:
        if item.get("kty") != "RSA" or item.get("alg") not in {None, "RS256"}:
            continue
        kid = item.get("kid")
        if not kid or "n" not in item or "e" not in item:
            continue
        modulus = int.from_bytes(_b64(item["n"]), "big")
        exponent = int.from_bytes(_b64(item["e"]), "big")
        keys[kid] = RSAPublicNumbers(exponent, modulus).public_key()
    return keys


def _split(token: str):
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("jwt")
    signing_input = (parts[0] + "." + parts[1]).encode("ascii")
    header = json.loads(_b64(parts[0]))
    payload = json.loads(_b64(parts[1]))
    signature = _b64(parts[2])
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise ValueError("jwt")
    return signing_input, header, payload, signature


def _b64(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
