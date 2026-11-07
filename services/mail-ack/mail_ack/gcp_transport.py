"""Network helpers. Tests do not import the live request function."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen


def urllib_transport(timeout: float = 20):
    def request(method: str, url: str, headers: dict, body: bytes | None):
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=timeout) as response:
                return response.status, response.read()
        except Exception as exc:
            status = getattr(exc, "code", None)
            if status is None:
                raise
            payload = exc.read() if hasattr(exc, "read") else b""
            return int(status), payload

    return request


def authorized_request():
    """Attach a metadata-server token. Used only on Cloud Run, never in unit tests."""
    inner = urllib_transport()

    def request(method: str, url: str, headers: dict, body: bytes | None):
        token = _metadata_token()
        merged = dict(headers)
        merged["Authorization"] = f"Bearer {token}"
        return inner(method, url, merged, body)

    return request


def _metadata_token() -> str:
    req = Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urlopen(req, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    token = data.get("access_token")
    if not token:
        raise RuntimeError("metadata_token_missing")
    return token
