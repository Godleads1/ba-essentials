"""The visitor's IP address, for rate limits and audit records.

`X-Forwarded-For` is written by whoever sent the request, so its left-hand entries can be
anything a visitor likes. Only the entries added by our own proxies can be trusted:

- CLIENT_IP_HEADER: a header that the edge proxy sets itself and overwrites, such as
  Cloudflare's `CF-Connecting-IP` (setting value "HTTP_CF_CONNECTING_IP"). Use it only when
  the server can be reached through that proxy alone.
- TRUSTED_PROXY_COUNT: how many of our proxies append to `X-Forwarded-For` (for example 1
  for a single nginx or load balancer). The client is the entry that many places from the right.
- Neither set: `REMOTE_ADDR`, the machine that connected to us.
"""
from __future__ import annotations

import ipaddress

from django.conf import settings


def _valid(value: str) -> str:
    value = (value or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def client_ip(request) -> str:
    remote = request.META.get("REMOTE_ADDR", "")
    header = getattr(settings, "CLIENT_IP_HEADER", "")
    if header:
        found = _valid(request.META.get(header, ""))
        if found:
            return found
    count = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if count > 0:
        hops = [h.strip() for h in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if h.strip()]
        if len(hops) >= count:
            found = _valid(hops[-count])
            if found:
                return found
    return remote
