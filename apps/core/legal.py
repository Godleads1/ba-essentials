"""Versions of the legal pages, recorded with every agreement.

Change these when the published Terms of Service or Privacy Policy change.
"""
# The effective date shown on the Terms page. Set it to the day this version goes live.
TERMS_EFFECTIVE_DATE = "22 November 2026"
TERMS_VERSION = f"Terms 2 ({TERMS_EFFECTIVE_DATE})"
PRIVACY_VERSION = "Privacy 0.1 (12 Nov 2026)"
AGREEMENT_VERSION = f"{TERMS_VERSION}; {PRIVACY_VERSION}"

# Terms 10.1(b): a change to price, renewal, refunds, liability or personal data must be confirmed
# by the client before they can pay for a new period. An agreement counts if its version starts
# with one of these. For a later update that doesn't need confirming, add its prefix here too,
# so people who signed up under it aren't asked again.
TERMS_TO_CONFIRM = ("Terms 2 (",)

# What changed, shown on the confirmation page. Plain words; the Terms themselves are the record.
TERMS_CHANGES = (
    "Yearly plans are reminded 14 days before they end (monthly plans: 3 days), by email and in your account.",
    "Yearly prices are the totals shown in the Terms: Starter ₦660,000, Growth ₦1,053,000, Dedicated ₦2,808,000.",
    ("Requests sent during the 5-business-day grace period are held, use your next period's allowance and start "
     "once you pay. Work already in progress continues."),
    ("If you pay during the grace period, your next period starts when the last one ended; if you pay after your "
     "account is paused, it starts on the day you pay."),
    ("While paused you can still update your details, make data-protection requests and complaints, and close "
     "your account."),
    "Refunds for a late task that used more than one task of your allowance return all of them.",
    ("We give 14 days' notice of changes to these terms, and ask you to confirm important ones before your next "
     "payment."),
)
