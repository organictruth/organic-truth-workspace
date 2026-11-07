"""Exclusion-only content classes.

Keyword matches can only deny. They never grant eligibility. The security
boundary is verified provenance plus singleton business delivery.
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)

# Each pattern is a denial category. "order" is deliberately absent.
_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "security_verification",
        re.compile(
            r"\b(?:password|passcode|one[- ]time code|verification code|"
            r"security code|two[- ]factor|2fa|mfa)\b|\bssn\b|social security",
            re.I,
        ),
    ),
    (
        "bank",
        re.compile(
            r"\b(?:bank account|routing number|iban|swift code|account number)\b",
            re.I,
        ),
    ),
    (
        "payment",
        re.compile(
            r"\b(?:invoice|wire transfer|credit card|debit card|cvv|chargeback|"
            r"payment|billing statement)\b",
            re.I,
        ),
    ),
    (
        "legal",
        re.compile(
            r"\b(?:lawsuit|subpoena|litigation|attorney|legal notice)\b|"
            r"cease and desist",
            re.I,
        ),
    ),
    (
        "injury",
        re.compile(r"\binjur(?:y|ed|ies)\b|\bmalpractice\b", re.I),
    ),
    (
        "medical",
        re.compile(r"\b(?:diagnosis|prescription|hipaa|medical|patient)\b", re.I),
    ),
)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).translate(_ZERO_WIDTH)
    return text.replace("\x00", "")


def risk_category(text: str) -> str | None:
    folded = normalize_text(text)
    for name, pattern in _CATEGORIES:
        if pattern.search(folded):
            return name
    return None
