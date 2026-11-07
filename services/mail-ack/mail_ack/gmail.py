"""Gmail REST client for read, watch, history, and send."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import json
from urllib.parse import urlencode

from mail_ack.eligibility import FetchProof
from mail_ack.pipeline import SendResult


class GmailError(Exception):
    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class HistoryExpired(GmailError):
    pass


@dataclass(frozen=True)
class Fetched:
    proof: FetchProof
    raw: bytes


class GmailClient:
    def __init__(self, *, token: dict, transport, mailbox: str):
        self._token = dict(token)
        self._transport = transport
        self.mailbox = mailbox
        self._access = token.get("access_token") or ""

    def send(self, raw: bytes) -> SendResult:
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        try:
            status, body = self._json(
                "POST",
                "/gmail/v1/users/me/messages/send",
                {"raw": encoded},
            )
        except GmailError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                return SendResult("rejected")
            return SendResult("uncertain")
        except (TimeoutError, OSError):
            return SendResult("uncertain")
        if status != 200:
            if 400 <= status < 500:
                return SendResult("rejected")
            return SendResult("uncertain")
        try:
            remote = json.loads(body.decode("utf-8")).get("id")
        except Exception:
            return SendResult("uncertain")
        if not remote:
            return SendResult("uncertain")
        return SendResult("accepted", str(remote))

    def get_message(self, message_id: str) -> Fetched:
        status, body = self._json("GET", f"/gmail/v1/users/me/messages/{message_id}?format=raw")
        if status != 200:
            raise GmailError("get_failed", status)
        try:
            data = json.loads(body.decode("utf-8"))
            raw = base64.urlsafe_b64decode(_pad(data["raw"]))
        except Exception as exc:
            raise GmailError("get_malformed") from exc
        if "labelIds" not in data or "id" not in data or "threadId" not in data:
            labels = None
            message = data.get("id") or message_id
            thread = data.get("threadId") or ""
        else:
            labels = tuple(data["labelIds"])
            message = data["id"]
            thread = data["threadId"]
        if "labelIds" not in data:
            labels = None
        proof = FetchProof(
            source="gmail_api",
            mailbox=self.mailbox,
            message_id=str(message),
            thread_id=str(thread),
            label_ids=labels,
            history_id=None,
        )
        return Fetched(proof=proof, raw=raw)

    def profile_history_id(self) -> str:
        status, body = self._json("GET", "/gmail/v1/users/me/profile")
        if status != 200:
            raise GmailError("profile_failed", status)
        history_id = json.loads(body.decode("utf-8")).get("historyId")
        if not history_id:
            raise GmailError("profile_malformed")
        return str(history_id)

    def watch(self, topic: str) -> dict:
        status, body = self._json(
            "POST",
            "/gmail/v1/users/me/watch",
            {"topicName": topic},
        )
        if status != 200:
            raise GmailError("watch_failed", status)
        data = json.loads(body.decode("utf-8"))
        if "expiration" not in data or "historyId" not in data:
            raise GmailError("watch_malformed")
        return {"expiration": str(data["expiration"]), "historyId": str(data["historyId"])}

    def history_page(self, start: str, page_token: str | None) -> dict:
        query = {"startHistoryId": start, "historyTypes": "messageAdded", "maxResults": "100"}
        if page_token:
            query["pageToken"] = page_token
        status, body = self._json("GET", "/gmail/v1/users/me/history?" + urlencode(query))
        if status == 404:
            raise HistoryExpired("history_expired", 404)
        if status != 200:
            raise GmailError("history_failed", status)
        return json.loads(body.decode("utf-8"))

    def list_recent_ids(self, limit: int) -> list[str]:
        query = urlencode({"q": "newer_than:2d", "maxResults": str(limit)})
        status, body = self._json("GET", "/gmail/v1/users/me/messages?" + query)
        if status != 200:
            raise GmailError("list_failed", status)
        data = json.loads(body.decode("utf-8"))
        return [item["id"] for item in data.get("messages", []) if item.get("id")]

    def _json(self, method: str, path: str, payload: dict | None = None) -> tuple[int, bytes]:
        return self._request(method, path, payload, retry=True)

    def _request(self, method: str, path: str, payload: dict | None, retry: bool) -> tuple[int, bytes]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._access}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            status, response = self._transport.request(
                method, "https://gmail.googleapis.com" + path, headers, body
            )
        except TimeoutError:
            raise
        except OSError:
            raise
        if status == 401 and retry and self._refresh():
            return self._request(method, path, payload, retry=False)
        return status, response

    def _refresh(self) -> bool:
        refresh = self._token.get("refresh_token")
        client_id = self._token.get("client_id")
        client_secret = self._token.get("client_secret")
        if not refresh or not client_id or not client_secret:
            return False
        form = urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        ).encode("utf-8")
        try:
            status, response = self._transport.request(
                "POST",
                self._token.get("token_uri") or "https://oauth2.googleapis.com/token",
                {"Content-Type": "application/x-www-form-urlencoded"},
                form,
            )
        except (TimeoutError, OSError):
            return False
        if status != 200:
            return False
        try:
            access = json.loads(response.decode("utf-8")).get("access_token")
        except Exception:
            return False
        if not access:
            return False
        self._access = access
        return True


def load_token_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise GmailError("token_malformed")
    return data


def _pad(value: str) -> bytes:
    return value.encode("ascii") + b"=" * (-len(value) % 4)
