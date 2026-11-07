"""Environment configuration. Defaults keep sending off."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from mail_ack.constants import (
    BUSINESS_MAILBOX,
    OWNER_ACTIVATION_PHRASE,
    PERSONAL_ALIAS,
    SAME_MAILBOX_IDENTITIES,
)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


@dataclass(frozen=True)
class Config:
    mailbox: str = BUSINESS_MAILBOX
    personal_alias: str = PERSONAL_ALIAS
    sending_enabled: bool = False
    owner_activation: str = ""
    kill_file: str = ""
    db_path: str = "services/mail-ack/var/mail-ack.sqlite3"
    status_path: str = "services/mail-ack/var/mail-ack-status.json"
    gmail_token_path: str = ""
    arc_keys_path: str = ""
    pubsub_topic: str = ""
    push_audience: str = ""
    invoker_emails: tuple[str, ...] = ()
    oidc_jwks_url: str = ""
    oidc_jwks_path: str = ""
    bind: str = "127.0.0.1"
    port: int = 8787
    allow_public_bind: bool = False
    store_backend: str = "sqlite"
    firestore_project: str = ""
    account_identities: tuple[str, ...] = tuple(sorted(SAME_MAILBOX_IDENTITIES))

    def kill_file_engaged(self) -> bool:
        return bool(self.kill_file) and Path(self.kill_file).exists()

    def sending_configured(self) -> bool:
        return (
            self.sending_enabled
            and self.owner_activation == OWNER_ACTIVATION_PHRASE
            and not self.kill_file_engaged()
        )


def load_config() -> Config:
    emails = tuple(
        item.strip().lower()
        for item in os.environ.get("MAIL_ACK_INVOKER_EMAILS", "").split(",")
        if item.strip()
    )
    port_raw = os.environ.get("PORT") or os.environ.get("MAIL_ACK_PORT") or "8787"
    bind = os.environ.get("MAIL_ACK_BIND", "127.0.0.1")
    on_cloud_run = bool(os.environ.get("K_SERVICE"))
    return Config(
        mailbox=os.environ.get("MAIL_ACK_MAILBOX", BUSINESS_MAILBOX).strip().lower(),
        personal_alias=os.environ.get("MAIL_ACK_EXCLUDED_ALIAS", PERSONAL_ALIAS).strip().lower(),
        sending_enabled=_flag("MAIL_ACK_SENDING_ENABLED"),
        owner_activation=os.environ.get("MAIL_ACK_OWNER_ACTIVATION", ""),
        kill_file=os.environ.get("MAIL_ACK_KILL_FILE", ""),
        db_path=os.environ.get("MAIL_ACK_DB_PATH", "services/mail-ack/var/mail-ack.sqlite3"),
        status_path=os.environ.get("MAIL_ACK_STATUS_PATH", "services/mail-ack/var/mail-ack-status.json"),
        gmail_token_path=os.environ.get("MAIL_ACK_GMAIL_TOKEN_PATH", ""),
        arc_keys_path=os.environ.get("MAIL_ACK_ARC_KEYS_PATH", ""),
        pubsub_topic=os.environ.get("MAIL_ACK_PUBSUB_TOPIC", ""),
        push_audience=os.environ.get("MAIL_ACK_PUSH_AUDIENCE", ""),
        invoker_emails=emails,
        oidc_jwks_url=os.environ.get("MAIL_ACK_OIDC_JWKS_URL", ""),
        oidc_jwks_path=os.environ.get("MAIL_ACK_OIDC_JWKS_PATH", ""),
        bind=bind,
        port=int(port_raw),
        allow_public_bind=_flag("MAIL_ACK_ALLOW_PUBLIC_BIND") or on_cloud_run,
        store_backend=os.environ.get("MAIL_ACK_STORE", "sqlite").strip().lower(),
        firestore_project=os.environ.get("MAIL_ACK_FIRESTORE_PROJECT", ""),
    )


def effective_sending(config: Config, latch: dict | None) -> bool:
    if latch:
        return False
    return config.sending_configured()
