"""Shared settings for the app's own outgoing HTTPS calls (Paystack, Flutterwave, Termii).

Several providers sit behind Cloudflare, which refuses Python's default
"Python-urllib/x.y" User-Agent (Cloudflare error 1010, HTTP 403). Every
outgoing call sends this name instead, so providers see who is calling.
"""

USER_AGENT = "BandaEssentials/1.0 (+https://bandaessentials.com)"
