"""Send gate, dedup, cap, crash, and catch-up behavior."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import tempfile
import threading
import unittest

from mail_ack.config import Config
from mail_ack.constants import ACK_TEXT, DAILY_CAP, OWNER_ACTIVATION_PHRASE
from mail_ack.pipeline import SendResult, handle_message
from mail_ack.store import Store
from mail_ack.worker import baseline_if_needed, reconcile, renew_watch

from support import NOW, SELECTOR, generate_key, inquiry, proof

PRIVATE = generate_key()
KEYS = {SELECTOR: PRIVATE.public_key()}


class Spy:
    def __init__(self, kind="accepted"):
        self.kind = kind
        self.calls = []
        self._lock = threading.Lock()

    def send(self, raw):
        with self._lock:
            self.calls.append(raw)
        if self.kind == "raise":
            raise RuntimeError("boom")
        remote = "remote-1" if self.kind == "accepted" else None
        return SendResult(self.kind, remote)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = Config(
            sending_enabled=True,
            owner_activation=OWNER_ACTIVATION_PHRASE,
            db_path=str(root / "ack.sqlite3"),
            status_path=str(root / "status.json"),
        )
        self.store = Store(self.config.db_path)

    def test_eligible_message_sends_once_and_replay_does_not(self):
        spy = Spy()
        raw = inquiry(PRIVATE)
        first = handle_message(
            raw=raw, proof=proof(), config=self.config, store=self.store, transport=spy, keys=KEYS, now=NOW
        )
        second = handle_message(
            raw=raw, proof=proof(), config=self.config, store=self.store, transport=spy, keys=KEYS, now=NOW
        )
        self.assertEqual(first["outcome"], "sent")
        self.assertEqual(second["outcome"], "duplicate")
        self.assertEqual(len(spy.calls), 1)
        body = spy.calls[0].decode("utf-8")
        self.assertIn(ACK_TEXT, body)
        self.assertIn("Auto-Submitted: auto-replied", body)
        self.assertNotIn("unsubscribe", body.lower())

    def test_switch_off_does_not_send_and_does_not_send_later(self):
        off = Config(
            sending_enabled=False,
            owner_activation="",
            db_path=self.config.db_path,
            status_path=self.config.status_path,
        )
        spy = Spy()
        raw = inquiry(PRIVATE)
        held = handle_message(raw=raw, proof=proof(), config=off, store=self.store, transport=spy, keys=KEYS, now=NOW)
        later = handle_message(
            raw=raw, proof=proof(), config=self.config, store=self.store, transport=spy, keys=KEYS, now=NOW
        )
        self.assertEqual(held["outcome"], "held_switch_off")
        self.assertEqual(later["outcome"], "duplicate")
        self.assertEqual(spy.calls, [])

    def test_activation_phrase_alone_does_not_send(self):
        partial = Config(
            sending_enabled=False,
            owner_activation=OWNER_ACTIVATION_PHRASE,
            db_path=self.config.db_path,
            status_path=self.config.status_path,
        )
        spy = Spy()
        result = handle_message(
            raw=inquiry(PRIVATE), proof=proof(), config=partial, store=self.store, transport=spy, keys=KEYS, now=NOW
        )
        self.assertEqual(result["outcome"], "held_switch_off")
        self.assertEqual(spy.calls, [])

    def test_kill_file_overrides_both_flags(self):
        kill = Path(self.tmp.name) / "KILL"
        kill.write_text("stop", encoding="utf-8")
        config = Config(
            sending_enabled=True,
            owner_activation=OWNER_ACTIVATION_PHRASE,
            kill_file=str(kill),
            db_path=self.config.db_path,
            status_path=self.config.status_path,
        )
        spy = Spy()
        result = handle_message(
            raw=inquiry(PRIVATE), proof=proof(), config=config, store=self.store, transport=spy, keys=KEYS, now=NOW
        )
        self.assertEqual(result["outcome"], "held_switch_off")
        self.assertEqual(spy.calls, [])

    def test_sender_window_and_thread(self):
        spy = Spy()
        handle_message(
            raw=inquiry(PRIVATE),
            proof=proof("m-a", "t-a"),
            config=self.config,
            store=self.store,
            transport=spy,
            keys=KEYS,
            now=NOW,
        )
        second = handle_message(
            raw=inquiry(PRIVATE),
            proof=proof("m-b", "t-b"),
            config=self.config,
            store=self.store,
            transport=spy,
            keys=KEYS,
            now=NOW + timedelta(days=1),
        )
        same_thread = handle_message(
            raw=inquiry(PRIVATE, from_header="Other <other@other.example>", header_from="other.example"),
            proof=proof("m-c", "t-a"),
            config=self.config,
            store=self.store,
            transport=spy,
            keys=KEYS,
            now=NOW + timedelta(days=5),
        )
        self.assertEqual(second["outcome"], "suppressed_sender_window")
        self.assertEqual(same_thread["outcome"], "suppressed_thread")
        self.assertEqual(len(spy.calls), 1)

    def test_cap_latches_without_external_alert(self):
        spy = Spy()
        for index in range(DAILY_CAP + 3):
            handle_message(
                raw=inquiry(
                    PRIVATE,
                    from_header=f"Buyer <buyer{index}@buyer.example>",
                    header_from="buyer.example",
                ),
                proof=proof(f"m-{index}", f"t-{index}"),
                config=self.config,
                store=self.store,
                transport=spy,
                keys=KEYS,
                now=NOW,
            )
        self.assertEqual(len(spy.calls), DAILY_CAP)
        latch = self.store.latch()
        self.assertTrue(latch["latched"])
        self.assertEqual(latch["alert"], "status_record_only")
        status = Path(self.config.status_path).read_text(encoding="utf-8")
        self.assertNotIn("Hello, I have a question", status)
        self.assertNotIn("@buyer.example", status)

    def test_concurrent_workers_stop_at_the_cap(self):
        spy = Spy()
        width = DAILY_CAP + 5
        raws = [
            inquiry(
                PRIVATE,
                from_header=f"Buyer <buyer{index}@buyer.example>",
                header_from="buyer.example",
            )
            for index in range(width)
        ]
        barrier = threading.Barrier(width)

        def worker(index):
            barrier.wait()
            handle_message(
                raw=raws[index],
                proof=proof(f"c-{index}", f"ct-{index}"),
                config=self.config,
                store=self.store,
                transport=spy,
                keys=KEYS,
                now=NOW,
            )

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(width)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(spy.calls), DAILY_CAP)
        self.assertTrue(self.store.latch()["latched"])

    def test_concurrent_same_message_sends_once(self):
        spy = Spy()
        raw = inquiry(PRIVATE)
        barrier = threading.Barrier(8)
        outcomes = []

        def worker():
            barrier.wait()
            outcomes.append(
                handle_message(
                    raw=raw, proof=proof(), config=self.config, store=self.store, transport=spy, keys=KEYS, now=NOW
                )["outcome"]
            )

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("sent"), 1)
        self.assertEqual(len(spy.calls), 1)

    def test_crash_after_attempt_is_not_retried(self):
        crashing = Spy("raise")
        raw = inquiry(PRIVATE)
        result = handle_message(
            raw=raw, proof=proof(), config=self.config, store=self.store, transport=crashing, keys=KEYS, now=NOW
        )
        self.assertEqual(result["outcome"], "send_uncertain")
        healthy = Spy()
        again = handle_message(
            raw=raw, proof=proof(), config=self.config, store=self.store, transport=healthy, keys=KEYS, now=NOW
        )
        self.assertEqual(again["outcome"], "duplicate")
        self.assertEqual(healthy.calls, [])
        self.store.reserve(
            message_id="m-killed",
            thread_id="t-killed",
            sender_key="other@other.example",
            sending_allowed=True,
            now=NOW,
        )
        self.assertTrue(self.store.mark_attempted("m-killed", NOW)["ok"])
        killed = Spy()
        resumed = handle_message(
            raw=raw,
            proof=proof("m-killed", "t-killed"),
            config=self.config,
            store=self.store,
            transport=killed,
            keys=KEYS,
            now=NOW,
        )
        self.assertEqual(resumed["outcome"], "send_not_retried")
        self.assertEqual(killed.calls, [])

    def test_crash_after_reserve_can_still_send_once(self):
        raw = inquiry(PRIVATE)
        self.store.reserve(
            message_id="m-1",
            thread_id="t-1",
            sender_key="buyer@buyer.example",
            sending_allowed=True,
            now=NOW,
        )
        spy = Spy()
        result = handle_message(
            raw=raw, proof=proof(), config=self.config, store=self.store, transport=spy, keys=KEYS, now=NOW
        )
        self.assertEqual(result["outcome"], "sent")
        self.assertEqual(len(spy.calls), 1)

    def test_rejected_send_is_not_retried(self):
        spy = Spy("rejected")
        raw = inquiry(PRIVATE)
        first = handle_message(
            raw=raw, proof=proof(), config=self.config, store=self.store, transport=spy, keys=KEYS, now=NOW
        )
        second = handle_message(
            raw=raw, proof=proof(), config=self.config, store=self.store, transport=Spy(), keys=KEYS, now=NOW
        )
        self.assertEqual(first["outcome"], "send_rejected")
        self.assertEqual(second["outcome"], "duplicate")

    def test_baseline_does_not_backfill(self):
        class FakeGmail:
            def __init__(self):
                self.sent = []

            def profile_history_id(self):
                return "500"

            def send(self, raw):
                self.sent.append(raw)
                return SendResult("accepted", "x")

        client = FakeGmail()
        self.assertTrue(baseline_if_needed(client, self.store))
        self.assertEqual(self.store.cursor()["history_id"], "500")
        self.assertEqual(client.sent, [])

    def test_history_catchup_respects_the_switch(self):
        raw = inquiry(PRIVATE)

        class FakeGmail:
            def history_page(self, start, page_token):
                return {
                    "history": [{"id": "12", "messagesAdded": [{"message": {"id": "m-9", "threadId": "t-9"}}]}],
                    "historyId": "12",
                }

            def get_message(self, message_id):
                from mail_ack.gmail import Fetched

                return Fetched(proof(message_id, "t-9"), raw)

            def send(self, payload):
                raise AssertionError("send")

        self.store.put_cursor("10", None, advance=False)
        off = Config(db_path=self.config.db_path, status_path=self.config.status_path)
        result = reconcile(FakeGmail(), self.store, off, KEYS, NOW)
        self.assertEqual(result["action"], "history")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(self.store.message("m-9")["state"], "held_switch_off")


class RenewTests(unittest.TestCase):
    def test_watch_renewal_does_not_skip_history(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "ack.sqlite3")
        store.put_cursor("40", None, advance=False)

        class FakeGmail:
            def watch(self, topic):
                return {"historyId": "99", "expiration": "1700000000000"}

        result = renew_watch(FakeGmail(), store, "projects/example/topics/mail-ack")
        self.assertEqual(result["action"], "watch_renewed")
        self.assertEqual(store.cursor()["history_id"], "40")
        self.assertEqual(store.cursor()["watch_expiration_ms"], "1700000000000")


if __name__ == "__main__":
    unittest.main()
