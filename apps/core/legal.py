"""Versions of the legal pages, recorded with every agreement.

Change these when the published Terms of Service or Privacy Policy change.
"""
# The effective date shown on the Terms page. Set it to the day this version goes live.
TERMS_EFFECTIVE_DATE = "26 November 2026"
# 2.2: first-reply targets by plan and queue priority for Growth and Dedicated (3.6). Not a
# 10.1(b) change, so nobody is asked to confirm it.
# 2.1: contact addresses (complaints@ for 5.6.8 and 6.5; refunds@ and counselling in 10.5) and the
# contracting party's description in 1.1. No change to price, renewal, refunds, liability or data use.
TERMS_VERSION = f"Terms 2.2 ({TERMS_EFFECTIVE_DATE})"
PRIVACY_VERSION = "Privacy 0.2 (26 Nov 2026)"
AGREEMENT_VERSION = f"{TERMS_VERSION}; {PRIVACY_VERSION}"

# Terms 10.1(b): a change to price, renewal, refunds, liability or personal data must be confirmed
# by the client before they can pay for a new period. An agreement counts if its version starts
# with one of these. For a later update that doesn't need confirming, add its prefix here too,
# so people who signed up under it aren't asked again.
TERMS_TO_CONFIRM = ("Terms 2 (", "Terms 2.1 (", "Terms 2.2 (")

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
