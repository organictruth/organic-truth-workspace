"""HTTP, Firestore adapter, cost arithmetic, and deploy-default checks."""

from __future__ import annotations

from datetime import datetime, timezone
import base64
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

from mail_ack.config import Config
from mail_ack.constants import BUSINESS_MAILBOX, OWNER_ACTIVATION_PHRASE
from mail_ack.costs import list_price, low_volume
from mail_ack.firestore_store import FirestoreStore
from mail_ack.httpapi import handle_request
from mail_ack.oidc import verify_oidc
from mail_ack.store import Store

NOW = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[3]


class FakeFirestore:
    def __init__(self):
        self.docs = {}
        self.serial = 0

    def request(self, method, url, headers, body):
        del method, headers
        payload = json.loads(body.decode("utf-8"))
        if url.endswith(":beginTransaction"):
            self.serial += 1
            return 200, json.dumps({"transaction": str(self.serial)}).encode()
        if url.endswith(":batchGet"):
            rows = []
            for name in payload["documents"]:
                if name in self.docs:
                    rows.append({"found": {"name": name, "fields": {"json": {"stringValue": self.docs[name]}}}})
                else:
                    rows.append({"missing": name})
            return 200, json.dumps(rows).encode()
        if url.endswith(":commit"):
            for write in payload["writes"]:
                if "delete" in write:
                    self.docs.pop(write["delete"], None)
                else:
                    name = write["update"]["name"]
                    self.docs[name] = write["update"]["fields"]["json"]["stringValue"]
            return 200, b"{}"
        return 404, b""


class EdgeTests(unittest.TestCase):
    def test_firestore_reserve_matches_sqlite(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sqlite = Store(Path(tmp) / "ack.sqlite3")
            remote = FakeFirestore()
            firestore = FirestoreStore("example-project", remote.request)
            kwargs = dict(
                message_id="m-1",
                thread_id="t-1",
                sender_key="buyer@buyer.example",
                sending_allowed=True,
                now=NOW,
            )
            self.assertEqual(sqlite.reserve(**kwargs)["code"], "reserved")
            self.assertEqual(firestore.reserve(**kwargs)["code"], "reserved")
            self.assertEqual(sqlite.reserve(**kwargs)["code"], "duplicate")
            self.assertEqual(firestore.reserve(**kwargs)["code"], "duplicate")
            attempted = firestore.mark_attempted("m-1", NOW)
            self.assertTrue(attempted["ok"])
            self.assertEqual(firestore.message("m-1")["state"], "send_attempted")

    def test_oidc_and_push_fail_closed(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = private.public_key()
        numbers = public.public_numbers()
        self.assertIsInstance(numbers, RSAPublicNumbers)
        token = _jwt(
            private,
            "kid-1",
            {
                "iss": "https://accounts.google.com",
                "aud": "https://mail-ack.example.run.app",
                "exp": int(NOW.timestamp()) + 600,
                "email": "push@example.iam.gserviceaccount.com",
                "email_verified": True,
            },
        )
        keys = {"kid-1": public}
        allowed = {"push@example.iam.gserviceaccount.com"}
        self.assertEqual(
            verify_oidc(
                f"Bearer {token}",
                keys_by_kid=keys,
                audience="https://mail-ack.example.run.app",
                allowed_emails=allowed,
                now=NOW.timestamp(),
            ),
            "push@example.iam.gserviceaccount.com",
        )
        self.assertIsNone(
            verify_oidc(
                f"Bearer {token}",
                keys_by_kid=keys,
                audience="https://other.example",
                allowed_emails=allowed,
                now=NOW.timestamp(),
            )
        )
        self.assertIsNone(verify_oidc(None, keys_by_kid=keys, audience="x", allowed_emails=allowed))

    def test_push_baseline_does_not_send(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(db_path=str(Path(tmp) / "ack.sqlite3"), status_path=str(Path(tmp) / "status.json"))
            store = Store(config.db_path)

            class Client:
                def profile_history_id(self):
                    return "77"

                def send(self, raw):
                    raise AssertionError(raw)

            notice = {
                "emailAddress": BUSINESS_MAILBOX,
                "historyId": "80",
            }
            body = json.dumps(
                {
                    "message": {
                        "data": base64.urlsafe_b64encode(json.dumps(notice).encode()).decode(),
                        "messageId": "pubsub-1",
                    }
                }
            ).encode()
            status, payload = handle_request(
                "POST",
                "/push",
                {"Authorization": "Bearer test"},
                body,
                config=config,
                store=store,
                client=Client(),
                keys={},
                verify=lambda header: "push@example.iam.gserviceaccount.com" if header else None,
                now=NOW,
            )
            self.assertEqual(status, 204)
            self.assertEqual(payload["action"], "baseline")
            self.assertEqual(store.cursor()["history_id"], "77")
            denied, _ = handle_request(
                "POST",
                "/push",
                {},
                body,
                config=config,
                store=store,
                client=Client(),
                keys={},
                verify=lambda header: None,
                now=NOW,
            )
            self.assertEqual(denied, 401)

    def test_cost_scenario_is_zero_only_inside_free_tier(self):
        scenario = low_volume()
        inside = list_price(scenario, free_tiers_remaining=True)
        self.assertTrue(all(value == 0 for value in inside.values()))
        outside = list_price(scenario, free_tiers_remaining=False)
        self.assertGreater(outside["cloud_run"], 0)
        self.assertLess(sum(outside.values()), 1)

    def test_deploy_files_keep_sending_off(self):
        deploy = Path(__file__).resolve().parents[1] / "deploy"
        text = "\n".join(path.read_text(encoding="utf-8") for path in deploy.rglob("*") if path.is_file())
        self.assertNotIn("MAIL_ACK_SENDING_ENABLED=true", text)
        self.assertNotIn(f'MAIL_ACK_OWNER_ACTIVATION = "{OWNER_ACTIVATION_PHRASE}"', text)
        self.assertIn("confirm_billable_apply", text)
        self.assertNotIn("allUsers", text)
        gmail = (Path(__file__).resolve().parents[1] / "mail_ack" / "gmail.py").read_text(encoding="utf-8")
        self.assertNotIn("/trash", gmail)
        self.assertNotIn("messages.delete", gmail)
        self.assertNotIn("batchModify", gmail)

    def test_repository_has_no_secret_material(self):
        root = Path(__file__).resolve().parents[1] / "mail_ack"
        blob = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore") for path in root.rglob("*") if path.is_file()
        )
        self.assertNotIn("-----BEGIN PRIVATE KEY-----", blob)
        self.assertNotIn("ya29.", blob)


def _jwt(private, kid, payload):
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}

    def encode(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    signing = f"{encode(header)}.{encode(payload)}".encode("ascii")
    signature = private.sign(signing, padding.PKCS1v15(), hashes.SHA256())
    tail = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return signing.decode("ascii") + "." + tail


if __name__ == "__main__":
    unittest.main()
