"""Firestore-backed store for Cloud Run. Instance disk is not durable."""

from __future__ import annotations

import json
from urllib.parse import quote

from mail_ack.store import Store


class FirestoreError(Exception):
    pass


class FirestoreStore(Store):
    """Same state machine as SQLite, committed with a Firestore transaction."""

    def __init__(self, project: str, request, database: str = "(default)"):
        if not project:
            raise FirestoreError("project_missing")
        self.project = project
        self.database = database
        self._request = request
        self.path = ""

    def get(self, key: str) -> dict | None:
        found = self._batch_get([key], None)
        return found.get(key)

    def _run(self, keys, fn):
        last_status = 0
        for _ in range(5):
            status, body = self._post(":beginTransaction", {"options": {"readWrite": {}}})
            if status != 200:
                raise FirestoreError("begin_failed")
            transaction = json.loads(body.decode("utf-8"))["transaction"]
            docs = self._batch_get(keys, transaction)
            updates, result = fn(docs)
            if not updates:
                return result
            commit_status = self._commit(transaction, updates)
            last_status = commit_status
            if commit_status == 200:
                return result
            if commit_status == 409:
                continue
            raise FirestoreError("commit_failed")
        raise FirestoreError(f"contention:{last_status}")

    def _batch_get(self, keys: list[str], transaction: str | None) -> dict[str, dict | None]:
        names = [self._name(key) for key in keys]
        payload: dict = {"documents": names}
        if transaction is not None:
            payload["transaction"] = transaction
        status, body = self._post(":batchGet", payload)
        if status != 200:
            raise FirestoreError("read_failed")
        rows = json.loads(body.decode("utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("found") or rows.get("missing") or []
            if isinstance(rows, dict):
                rows = [rows]
        found: dict[str, dict | None] = {key: None for key in keys}
        by_name = {self._name(key): key for key in keys}
        for row in rows:
            if "found" in row:
                name = row["found"]["name"]
                raw = row["found"]["fields"]["json"]["stringValue"]
                found[by_name[name]] = json.loads(raw)
            elif "missing" in row:
                found[by_name[row["missing"]]] = None
        return found

    def _commit(self, transaction: str, updates: dict) -> int:
        writes = []
        for key, value in updates.items():
            name = self._name(key)
            if value is None:
                writes.append({"delete": name})
            else:
                writes.append(
                    {
                        "update": {
                            "name": name,
                            "fields": {
                                "json": {
                                    "stringValue": json.dumps(value, sort_keys=True, separators=(",", ":"))
                                }
                            },
                        }
                    }
                )
        status, _body = self._post(":commit", {"transaction": transaction, "writes": writes})
        return status

    def _post(self, suffix: str, payload: dict) -> tuple[int, bytes]:
        url = (
            "https://firestore.googleapis.com/v1/projects/"
            f"{quote(self.project, safe='')}/databases/{quote(self.database, safe='')}/documents{suffix}"
        )
        return self._request(
            "POST",
            url,
            {"Content-Type": "application/json"},
            json.dumps(payload).encode("utf-8"),
        )

    def _name(self, key: str) -> str:
        collection, doc_id = _split(key)
        return (
            f"projects/{self.project}/databases/{self.database}/documents/"
            f"{collection}/{doc_id}"
        )


def _split(key: str) -> tuple[str, str]:
    if key.startswith("control/"):
        return "control", key.split("/", 1)[1]
    if ":" not in key:
        raise FirestoreError("bad_key")
    collection, doc_id = key.split(":", 1)
    if not collection or not doc_id or "/" in doc_id:
        raise FirestoreError("bad_key")
    return collection, doc_id
