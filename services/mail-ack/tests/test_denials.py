"""Denial paths for mail that must stay in the mailbox untouched."""

from __future__ import annotations

import unittest

from mail_ack.constants import ACK_TEXT, BUSINESS_MAILBOX, PERSONAL_ALIAS
from mail_ack.eligibility import PermitError, evaluate
from mail_ack.reply import build_reply

from support import NOW, SELECTOR, generate_key, inquiry, proof

PRIVATE = generate_key()
KEYS = {SELECTOR: PRIVATE.public_key()}


def decide(raw, **kwargs):
    return evaluate(
        raw,
        kwargs.get("fetch", proof()),
        KEYS,
        now=NOW.timestamp(),
    )


class DenialTests(unittest.TestCase):
    def test_order_question_is_not_high_risk(self):
        decision = decide(inquiry(PRIVATE, body="Please help me find an order.\r\n"))
        self.assertTrue(decision.eligible, decision.reason)

    def test_injury_payment_bank_and_legal_are_denied(self):
        samples = {
            "injury": "A family member was injured and I am writing about it.\r\n",
            "payment": "Please look at this invoice and payment.\r\n",
            "bank": "The bank account number is in my note.\r\n",
            "legal": "This is a legal notice about a lawsuit.\r\n",
            "medical": "The diagnosis is in the attached note.\r\n",
            "security_verification": "Here is the password reset code.\r\n",
        }
        for category, body in samples.items():
            decision = decide(inquiry(PRIVATE, body=body))
            self.assertEqual(decision.reason, category, body)

    def test_reply_to_does_not_change_the_recipient(self):
        raw = inquiry(PRIVATE, extra_after=(("Reply-To", "attacker@evil.example"),))
        decision = decide(raw)
        self.assertTrue(decision.eligible, decision.reason)
        outbound = build_reply(decision.permit, NOW)
        from mail_ack.headers import all_values, parse_message

        headers, body = parse_message(outbound)
        self.assertEqual(all_values(headers, "to"), ("buyer@buyer.example",))
        self.assertEqual(all_values(headers, "reply-to"), ())
        self.assertEqual(all_values(headers, "cc"), ())
        self.assertEqual(all_values(headers, "bcc"), ())
        self.assertIn(ACK_TEXT, body.decode("utf-8"))
        self.assertNotIn("attacker@evil.example", outbound.decode("utf-8"))
        self.assertNotIn(PERSONAL_ALIAS, outbound.decode("utf-8"))

    def test_reply_to_alias_is_not_used(self):
        raw = inquiry(PRIVATE, extra_after=(("Reply-To", PERSONAL_ALIAS),))
        decision = decide(raw)
        self.assertTrue(decision.eligible, decision.reason)
        outbound = build_reply(decision.permit, NOW).decode("utf-8")
        self.assertIn("To: buyer@buyer.example", outbound)
        self.assertNotIn(PERSONAL_ALIAS, outbound)

    def test_plan_reply_rejects_a_mapping(self):
        with self.assertRaises(PermitError):
            build_reply({"sender": "buyer@buyer.example"}, NOW)

    def test_malformed_input_fails_closed(self):
        decision = decide(b"this is not a message")
        self.assertFalse(decision.eligible)
        decision = decide(b"From: bad\r\n\x00\r\n\r\nHi\r\n")
        self.assertFalse(decision.eligible)

    def test_inbound_bcc_is_not_copied_and_multi_from_is_denied(self):
        raw = inquiry(PRIVATE, extra_after=(("Bcc", "victim@evil.example"),))
        decision = decide(raw)
        self.assertTrue(decision.eligible, decision.reason)
        outbound = build_reply(decision.permit, NOW).decode("utf-8")
        self.assertNotIn("victim@evil.example", outbound)
        self.assertNotIn("Bcc:", outbound)
        multi = decide(inquiry(PRIVATE, from_header="buyer@buyer.example, other@buyer.example"))
        self.assertFalse(multi.eligible)

    def test_loops_bulk_and_autoresponders(self):
        cases = [
            {"extra_after": (("Auto-Submitted", "auto-replied"),)},
            {"extra_after": (("X-Organic-Truth-Auto-Ack", "1"),)},
            {"extra_after": (("List-Id", "<news.buyer.example>"),)},
            {"extra_after": (("Precedence", "bulk"),)},
            {"extra_after": (("Resent-From", "other@buyer.example"),)},
        ]
        for case in cases:
            decision = decide(inquiry(PRIVATE, **case))
            self.assertFalse(decision.eligible, case)

    def test_noreply_own_domain_and_alias_senders(self):
        cases = [
            ("No Reply <no-reply@buyer.example>", "buyer.example", "sender_not_allowed"),
            ("Staff <person@organictruthllc.com>", "organictruthllc.com", "sender_not_allowed"),
            (f"Owner <{PERSONAL_ALIAS}>", "gmail.com", "sender_not_allowed"),
        ]
        for from_header, aligned, reason in cases:
            decision = decide(inquiry(PRIVATE, from_header=from_header, header_from=aligned))
            self.assertEqual(decision.reason, reason, from_header)

    def test_both_addresses_in_to_are_ambiguous(self):
        decision = decide(
            inquiry(PRIVATE, to_header=f"{BUSINESS_MAILBOX}, {PERSONAL_ALIAS}")
        )
        self.assertNotEqual(decision.reason, "eligible")
        self.assertFalse(decision.eligible)

    def test_spam_label_and_missing_labels(self):
        self.assertFalse(decide(inquiry(PRIVATE), fetch=proof(labels=("INBOX", "SPAM"))).eligible)
        self.assertFalse(decide(inquiry(PRIVATE), fetch=proof(labels=None)).eligible)

    def test_forwarded_arc_chain_is_denied(self):
        decision = decide(inquiry(PRIVATE, seal_i="2", seal_cv="pass"))
        self.assertFalse(decision.eligible)

    def test_unproven_fetch_source(self):
        fetched = proof()
        object.__setattr__(fetched, "source", "pasted")
        decision = evaluate(inquiry(PRIVATE), fetched, KEYS, now=NOW.timestamp())
        self.assertEqual(decision.reason, "unproven_fetch")


if __name__ == "__main__":
    unittest.main()
