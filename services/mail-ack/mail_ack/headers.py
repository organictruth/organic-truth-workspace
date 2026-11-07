"""Ordered header parsing. Duplicate header fields are preserved.

A dict keyed by header name is intentionally not used. Collapsing duplicates
would drop a second Authentication-Results or Delivered-To field.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


class HeaderError(Exception):
    """Malformed header block. Callers fail closed."""


@dataclass(frozen=True)
class Header:
    name: str
    value: str

    @property
    def lower_name(self) -> str:
        return self.name.lower()


_NAME_RE = re.compile(r"^[!#$%&'*+\-.0-9A-Z^_`a-z|~]{1,200}$")
_MAX_HEADERS = 400
_MAX_HEADER_BYTES = 256 * 1024
_MAX_RAW_BYTES = 2 * 1024 * 1024


def parse_message(raw: bytes) -> tuple[tuple[Header, ...], bytes]:
    """Return ordered headers and the unparsed body bytes."""
    if not isinstance(raw, (bytes, bytearray)):
        raise HeaderError("not_bytes")
    raw = bytes(raw)
    if len(raw) > _MAX_RAW_BYTES:
        raise HeaderError("message_too_large")
    if b"\x00" in raw:
        raise HeaderError("nul")
    sep, marker = _split_marker(raw)
    if sep < 0:
        raise HeaderError("no_header_terminator")
    head = raw[:sep]
    body = raw[sep + len(marker) :]
    if len(head) > _MAX_HEADER_BYTES:
        raise HeaderError("header_block_too_large")
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HeaderError("header_encoding") from exc
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    if "\r" in text:
        raise HeaderError("bare_cr")
    headers = _logical_headers(text)
    if len(headers) > _MAX_HEADERS:
        raise HeaderError("too_many_headers")
    return tuple(headers), body


def _split_marker(raw: bytes) -> tuple[int, bytes]:
    crlf = raw.find(b"\r\n\r\n")
    lf = raw.find(b"\n\n")
    if crlf >= 0 and (lf < 0 or crlf <= lf):
        return crlf, b"\r\n\r\n"
    if lf >= 0:
        return lf, b"\n\n"
    return -1, b""


def _logical_headers(text: str) -> list[Header]:
    logical: list[str] = []
    for line in text.split("\n"):
        if line.startswith((" ", "\t")):
            if not logical:
                raise HeaderError("leading_fold")
            logical[-1] = logical[-1] + line
            continue
        logical.append(line)
    headers: list[Header] = []
    for line in logical:
        if line == "":
            raise HeaderError("empty_header_line")
        if len(line) > 20000:
            raise HeaderError("header_line_too_long")
        if ":" not in line:
            raise HeaderError("missing_colon")
        name, value = line.split(":", 1)
        if not _NAME_RE.fullmatch(name):
            raise HeaderError("bad_header_name")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise HeaderError("control_char_in_value")
        headers.append(Header(name=name, value=value.strip(" \t")))
    if not headers:
        raise HeaderError("no_headers")
    return headers


def all_values(headers: tuple[Header, ...], name: str) -> tuple[str, ...]:
    wanted = name.lower()
    return tuple(header.value for header in headers if header.lower_name == wanted)


def first_value(headers: tuple[Header, ...], name: str) -> str | None:
    values = all_values(headers, name)
    return values[0] if values else None


def indexes(headers: tuple[Header, ...], name: str) -> tuple[int, ...]:
    wanted = name.lower()
    return tuple(i for i, header in enumerate(headers) if header.lower_name == wanted)
