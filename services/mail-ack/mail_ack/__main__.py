"""Operator CLI. It does not prompt for secrets and it does not enable sending."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

from mail_ack.config import load_config
from mail_ack.gmail import GmailClient, load_token_file
from mail_ack.httpapi import handle_request
from mail_ack.keys import load_arc_keys
from mail_ack.oidc import keys_from_jwks, verify_oidc
from mail_ack.store import Store
from mail_ack.worker import reconcile, renew_watch


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "self-check"
    config = load_config()
    if command == "self-check":
        return _self_check(config)
    if command == "status":
        return _status(config)
    if command == "clear-cap-latch":
        Store(config.db_path).clear_latch()
        print(json.dumps({"latch_cleared": True, "sending_configured": config.sending_configured()}))
        return 0
    if command == "serve":
        return _serve(config)
    if command in {"renew-watch", "reconcile"}:
        return _maintenance(config, command)
    print(json.dumps({"error": "unknown_command"}))
    return 2


def _self_check(config) -> int:
    report = {
        "sending_configured": config.sending_configured(),
        "sending_enabled_flag": config.sending_enabled,
        "owner_activation_present": bool(config.owner_activation),
        "kill_file_engaged": config.kill_file_engaged(),
        "store_backend": config.store_backend,
        "arc_keys_configured": bool(config.arc_keys_path),
        "gmail_token_configured": bool(config.gmail_token_path),
        "deployed": False,
    }
    print(json.dumps(report, sort_keys=True))
    return 0


def _status(config) -> int:
    if config.store_backend != "sqlite":
        print(json.dumps({"error": "status_requires_local_sqlite_or_parent_console"}))
        return 1
    store = Store(config.db_path)
    latch = store.latch()
    cursor = store.cursor()
    print(
        json.dumps(
            {
                "sending_configured": config.sending_configured(),
                "latch": latch,
                "cursor": cursor,
                "deployed": False,
            },
            sort_keys=True,
        )
    )
    return 0


def _maintenance(config, command: str) -> int:
    client, keys = _gmail(config)
    if client is None:
        print(json.dumps({"error": "gmail_not_configured"}))
        return 1
    store = Store(config.db_path)
    if command == "renew-watch":
        print(json.dumps(renew_watch(client, store, config.pubsub_topic), sort_keys=True))
        return 0
    print(json.dumps(reconcile(client, store, config, keys), sort_keys=True))
    return 0


def _serve(config) -> int:
    if config.bind in {"0.0.0.0", "::"} and not config.allow_public_bind:
        print(json.dumps({"error": "public_bind_refused"}))
        return 1
    client, keys = _gmail(config)
    verifier = _verifier(config)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self._dispatch()

        def do_POST(self):  # noqa: N802
            self._dispatch()

        def _dispatch(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            store = _store(config)
            status, payload = handle_request(
                self.command,
                self.path.split("?", 1)[0],
                {key: value for key, value in self.headers.items()},
                body,
                config=config,
                store=store,
                client=client,
                keys=keys,
                verify=verifier,
            )
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, fmt, *args):
            return

    if config.store_backend == "sqlite":
        Store(config.db_path)
    server = ThreadingHTTPServer((config.bind, config.port), Handler)
    print(json.dumps({"listening": True, "sending_configured": config.sending_configured()}))
    server.serve_forever()
    return 0


def _store(config):
    if config.store_backend == "firestore":
        from mail_ack.firestore_store import FirestoreStore
        from mail_ack.gcp_transport import authorized_request

        return FirestoreStore(config.firestore_project, authorized_request())
    return Store(config.db_path)


def _gmail(config):
    keys = load_arc_keys(config.arc_keys_path) if config.arc_keys_path else {}
    if not config.gmail_token_path:
        return None, keys
    from mail_ack.gcp_transport import urllib_transport

    token = load_token_file(config.gmail_token_path)
    return GmailClient(token=token, transport=urllib_transport(), mailbox=config.mailbox), keys


def _verifier(config):
    import time

    from mail_ack.oidc import fetch_jwks

    allowed = set(config.invoker_emails)
    keys = {}
    fetched_at = 0.0
    if config.oidc_jwks_path:
        keys = keys_from_jwks(json.loads(Path(config.oidc_jwks_path).read_text(encoding="utf-8")))

    def verify(authorization):
        nonlocal keys, fetched_at
        if config.oidc_jwks_url and (not keys or time.time() - fetched_at > 3600):
            try:
                keys = fetch_jwks(config.oidc_jwks_url)
                fetched_at = time.time()
            except Exception:
                if not keys:
                    return None
        return verify_oidc(
            authorization,
            keys_by_kid=keys,
            audience=config.push_audience,
            allowed_emails=allowed,
        )

    return verify


if __name__ == "__main__":
    raise SystemExit(main())
