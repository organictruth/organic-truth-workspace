"""Structured Authentication-Results parsing.

A substring search for dmarc=pass is not a verdict. Comments are removed
before method tokens are read, and only the supplied header value is parsed.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


class AuthParseError(Exception):
    pass


@dataclass(frozen=True)
class AuthVerdict:
    authserv_id: str
    methods: dict[str, str]
    properties: dict[str, str]


_METHOD_RESULTS = frozenset(
    {
        "none",
        "pass",
        "fail",
        "policy",
        "neutral",
        "temperror",
        "permerror",
        "softfail",
        "hardfail",
        "bestguesspass",
    }
)


def strip_comments(value: str) -> str:
    """Remove RFC 5322 comments. Unbalanced comments fail closed."""
    out: list[str] = []
    depth = 0
    in_quote = False
    escaped = False
    for char in value:
        if in_quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_quote = False
            continue
        if char == '"':
            in_quote = True
            out.append(char)
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            if depth == 0:
                raise AuthParseError("unbalanced_comment")
            depth -= 1
            continue
        if depth == 0:
            out.append(char)
    if depth != 0 or in_quote or escaped:
        raise AuthParseError("unbalanced_comment")
    return "".join(out)


def parse_authentication_results(value: str) -> AuthVerdict:
    try:
        text = strip_comments(value)
    except AuthParseError:
        raise
    text = text.strip()
    if not text or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise AuthParseError("empty_or_control")
    parts = [part.strip() for part in text.split(";") if part.strip()]
    if len(parts) < 2:
        raise AuthParseError("missing_methods")
    authserv = _authserv_id(parts[0])
    methods: dict[str, str] = {}
    properties: dict[str, str] = {}
    for part in parts[1:]:
        for token in part.split():
            if "=" not in token:
                continue
            name, raw_val = token.split("=", 1)
            name = name.strip().lower()
            raw_val = raw_val.strip().strip('"')
            if not name or not raw_val:
                raise AuthParseError("bad_token")
            if not re.fullmatch(r"[a-z0-9._-]{1,80}", name):
                raise AuthParseError("bad_token_name")
            if not re.fullmatch(r"[A-Za-z0-9._@:+/=-]{1,200}", raw_val):
                raise AuthParseError("bad_token_value")
            if "." in name:
                properties[name] = raw_val
                continue
            result = raw_val.lower()
            if result not in _METHOD_RESULTS:
                raise AuthParseError("bad_method_result")
            if name in methods:
                raise AuthParseError("duplicate_method")
            methods[name] = result
    if not methods:
        raise AuthParseError("no_methods")
    return AuthVerdict(authserv_id=authserv, methods=methods, properties=properties)


def _authserv_id(token: str) -> str:
    token = token.strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]{1,253}(?:/[a-z0-9._-]{1,32})?", token):
        raise AuthParseError("bad_authserv_id")
    identity = token.split("/", 1)[0].rstrip(".")
    if not identity or identity.startswith(".") or ".." in identity:
        raise AuthParseError("bad_authserv_id")
    return identity
