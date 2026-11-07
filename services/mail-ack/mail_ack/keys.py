"""Load pinned ARC public keys. A private key in this file is refused."""

from __future__ import annotations

import json
from pathlib import Path

from mail_ack.arcverify import load_public_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


def load_arc_keys(path: str) -> dict[str, RSAPublicKey]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    domain = data.get("google.com")
    if not isinstance(domain, dict):
        return {}
    keys: dict[str, RSAPublicKey] = {}
    for selector, pem in domain.items():
        if not isinstance(pem, str) or "PRIVATE" in pem:
            raise ValueError("arc_key_refused")
        keys[str(selector)] = load_public_key(pem)
    return keys
