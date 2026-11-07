"""Fixed policy values. These are not secrets and are not runtime toggles."""

BUSINESS_MAILBOX = "organictruth@organictruthllc.com"
PERSONAL_ALIAS = "organictruthllc@gmail.com"
# Same Google account local-part on the alternate consumer domain.
PERSONAL_ALIAS_GOOGLEMAIL = "organictruthllc@googlemail.com"

OWN_DOMAIN = "organictruthllc.com"

# Exact body. No templates, no model-generated text, no unsubscribe block.
ACK_TEXT = (
    "Hi, thank you for writing to Organic Truth. This is an automatic note to "
    "confirm we received your message. A member of our team will read it and "
    "reply personally. If it is about an order, including your order number "
    "helps us find it. Organic Truth LLC"
)

# Explicit opt-in phrase. Presence in source is not activation.
# Activation happens only when the deployed environment sets this exact value.
OWNER_ACTIVATION_PHRASE = "owner-activated-2026-11-07"

DAILY_CAP = 20
SENDER_WINDOW_SECONDS = 4 * 24 * 60 * 60
# Direct Google ARC seals older than this are not treated as fresh provenance.
ARC_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
ARC_FUTURE_SKEW_SECONDS = 24 * 60 * 60

# Attempted rows younger than this stay in progress. Older ones are uncertain.
ATTEMPT_STALE_SECONDS = 120

RECONCILE_MESSAGE_CAP = 50
HISTORY_PAGE_CAP = 20

GOOGLE_ARC_DOMAIN = "google.com"
GOOGLE_AUTHSERV_ID = "mx.google.com"
GOOGLE_MX_HOST = "mx.google.com"

AUTO_ACK_HEADER = "X-Organic-Truth-Auto-Ack"
AUTO_ACK_VALUE = "1"

# Fetch identity may be either address of the same mailbox.
# Neither value is evidence of which address the message was delivered to.
SAME_MAILBOX_IDENTITIES = frozenset(
    {BUSINESS_MAILBOX, PERSONAL_ALIAS, PERSONAL_ALIAS_GOOGLEMAIL}
)

DENIED_RECIPIENTS = frozenset(
    {BUSINESS_MAILBOX, PERSONAL_ALIAS, PERSONAL_ALIAS_GOOGLEMAIL}
)
