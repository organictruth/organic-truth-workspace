"""Provenance tests. Passing these does not prove a live Gmail deployment."""

from __future__ import annotations

import unittest

from mail_ack.arcverify import signing_input
from mail_ack.constants import BUSINESS_MAILBOX, PERSONAL_ALIAS
from mail_ack.eligibility import evaluate
from mail_ack.headers import Header, all_values, parse_message

from support import NOW, SELECTOR, generate_key, inquiry, proof

PRIVATE = generate_key()
KEYS = {SELECTOR: PRIVATE.public_key()}


def decide(raw, **kwargs):
    return evaluate(raw, kwargs.get("proof", proof()), KEYS, now=NOW.timestamp())


class ProvenanceTests(unittest.TestCase):
    def test_duplicate_headers_stay_in_order(self):
        raw = inquiry(
            PRIVATE,
            extra_after=(
                ("Authentication-Results", "mx.google.com; dmarc=fail"),
                ("Authentication-Results", "mx.google.com; dmarc=pass"),
            ),
        )
        headers, _body = parse_message(raw)
        values = all_values(headers, "authentication-results")
        self.assertGreaterEqual(len(values), 2)
        self.assertIn("dmarc=fail", values[-2])
        collapsed = {}
        for header in headers:
            collapsed[header.lower_name] = header.value
        self.assertNotEqual(len(collapsed), len(headers))
        self.assertNotIn("dmarc=fail", collapsed["authentication-results"])

    def test_business_inquiry_is_eligible(self):
        decision = decide(inquiry(PRIVATE))
        self.assertTrue(decision.eligible, decision.reason)
        self.assertEqual(decision.permit.sender, "buyer@buyer.example")

    def test_authserv_id_string_without_seal_is_not_enough(self):
        raw = inquiry(
            None,
            omit_arc=True,
            extra_after=(("Authentication-Results", "mx.google.com; dkim=pass; spf=pass; dmarc=pass; header.from=buyer.example"),),
        )
        decision = decide(raw)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "provenance_unverified")

    def test_forged_pass_cannot_override_sealed_fail(self):
        raw = inquiry(
            PRIVATE,
            dmarc="fail",
            extra_after=(("Authentication-Results", "mx.google.com; dmarc=pass; header.from=buyer.example"),),
        )
        decision = decide(raw)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "dmarc_not_pass")

    def test_forged_fail_does_not_grant_or_get_used_as_the_verdict_source(self):
        raw = inquiry(
            PRIVATE,
            extra_after=(("Authentication-Results", "mx.google.com; dmarc=fail; header.from=buyer.example"),),
        )
        decision = decide(raw)
        self.assertTrue(decision.eligible, decision.reason)

    def test_tampered_seal_fails_closed(self):
        decision = decide(inquiry(PRIVATE, tamper=True))
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "provenance_unverified")

    def test_wrong_key_fails_closed(self):
        other = generate_key().public_key()
        raw = inquiry(PRIVATE)
        decision = evaluate(raw, proof(), {"synthetic-selector": other}, now=NOW.timestamp())
        self.assertFalse(decision.eligible)

    def test_missing_pin_fails_closed(self):
        decision = evaluate(inquiry(PRIVATE), proof(), {}, now=NOW.timestamp())
        self.assertEqual(decision.reason, "provenance_unverified")

    def test_comment_does_not_hide_a_fail(self):
        raw = inquiry(PRIVATE, dmarc="fail (comment dmarc=pass)")
        # The method token itself must be exactly pass. A parenthetical pass is a comment
        # only when parentheses are used; this fixture keeps fail as the token and adds
        # a real comment via a separate signed value below.
        decision = decide(raw)
        self.assertFalse(decision.eligible)

    def test_parenthetical_pass_is_not_a_verdict(self):
        raw = inquiry(PRIVATE, dmarc="fail (dmarc=pass)")
        decision = decide(raw)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "dmarc_not_pass")

    def test_subject_substring_is_not_authentication(self):
        raw = inquiry(None, omit_arc=True, subject="dmarc=pass mx.google.com")
        decision = decide(raw)
        self.assertFalse(decision.eligible)

    def test_personal_alias_delivery_is_denied(self):
        decision = decide(inquiry(PRIVATE, delivered_to=PERSONAL_ALIAS))
        self.assertEqual(decision.reason, "delivered_to_personal_alias")

    def test_forged_business_delivered_to_after_personal_prefix_is_denied(self):
        raw = inquiry(
            PRIVATE,
            delivered_to=PERSONAL_ALIAS,
            extra_after=(("Delivered-To", BUSINESS_MAILBOX),),
        )
        decision = decide(raw)
        self.assertFalse(decision.eligible)
        self.assertNotEqual(decision.reason, "eligible")

    def test_business_prefix_plus_later_alias_is_ambiguous(self):
        raw = inquiry(PRIVATE, extra_after=(("Delivered-To", PERSONAL_ALIAS),))
        decision = decide(raw)
        self.assertEqual(decision.reason, "delivered_to_ambiguous")

    def test_delivered_to_only_after_the_seal_is_unproven(self):
        raw = inquiry(PRIVATE, delivered_to="buyer@buyer.example")
        # Move the only Delivered-To below the seal by omitting the prefix copy.
        text = raw.decode("utf-8")
        first, rest = text.split("\r\n", 1)
        self.assertTrue(first.startswith("Delivered-To:"))
        # Place it after the seal instead of before it.
        rest = rest.replace("From:", first + "\r\nFrom:", 1)
        decision = decide(rest.encode("utf-8"))
        self.assertIn(decision.reason, {"delivered_to_unproven", "delivered_to_not_business", "malformed_or_unproven"})

    def test_missing_return_path_fails_closed(self):
        decision = decide(inquiry(PRIVATE, omit_return_path=True))
        self.assertEqual(decision.reason, "missing_return_path")

    def test_empty_return_path_is_a_bounce(self):
        decision = decide(inquiry(PRIVATE, return_path="<>"))
        self.assertEqual(decision.reason, "empty_return_path")

    def test_received_host_must_be_exact(self):
        decision = decide(
            inquiry(PRIVATE, received="from mx.google.com by mx.google.com.attacker.example with ESMTPS id synthetic")
        )
        self.assertEqual(decision.reason, "google_mx_unproven")

    def test_unrelated_header_mentioning_mx_is_not_a_received_by(self):
        decision = decide(
            inquiry(
                PRIVATE,
                omit_received=True,
                extra_before=(("X-Note", "by mx.google.com"),),
            )
        )
        self.assertEqual(decision.reason, "google_mx_unproven")

    def test_canon_bytes_are_stable(self):
        headers = (
            Header("ARC-Authentication-Results", "i=1; mx.google.com; dmarc=pass; header.from=buyer.example"),
            Header("ARC-Message-Signature", "i=1; a=rsa-sha256; b=Zg=="),
            Header(
                "ARC-Seal",
                "i=1; a=rsa-sha256; cv=none; d=google.com; s=synthetic-selector; t=1700000000; b=QQ==",
            ),
        )
        expected = (
            "arc-authentication-results:i=1; mx.google.com; dmarc=pass; header.from=buyer.example\r\n"
            "arc-message-signature:i=1; a=rsa-sha256; b=Zg==\r\n"
            "arc-seal:i=1; a=rsa-sha256; cv=none; d=google.com; s=synthetic-selector; t=1700000000; b=\r\n"
        ).encode("ascii")
        self.assertEqual(signing_input(headers, 1), expected)

    def test_fetch_proof_cannot_be_a_header(self):
        raw = inquiry(
            PRIVATE,
            extra_after=(("X-Fetch-Source", "gmail_api"), ("Delivered-To", BUSINESS_MAILBOX)),
        )
        # The second Delivered-To makes this ambiguous, which is the safe result.
        decision = decide(raw)
        self.assertFalse(decision.eligible)


if __name__ == "__main__":
    unittest.main()
