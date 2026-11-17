from __future__ import annotations

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def client_ip_source(app_configs, **kwargs):
    """Behind a proxy, rate limits need the real visitor address (apps/core/client_ip.py)."""
    if getattr(settings, "CLIENT_IP_HEADER", "") or getattr(settings, "TRUSTED_PROXY_COUNT", 0):
        return []
    return [Warning(
        "Neither CLIENT_IP_HEADER nor TRUSTED_PROXY_COUNT is set.",
        hint="Behind a proxy or load balancer every visitor would share one address for rate limits. "
             "Set TRUSTED_PROXY_COUNT to the number of proxies in front of the app, or CLIENT_IP_HEADER "
             "to a header the edge proxy overwrites (HTTP_CF_CONNECTING_IP for Cloudflare).",
        id="banda.W001",
    )]


@register(Tags.security, deploy=True)
def security_alert_recipients(app_configs, **kwargs):
    """Security alerts (apps/audit/alerts.py) need somewhere to go."""
    if getattr(settings, "SECURITY_ALERT_EMAILS", None):
        return []
    return [Warning(
        "SECURITY_ALERT_EMAILS is empty, so security alerts are only logged.",
        hint="Set SECURITY_ALERT_EMAILS to the address(es) that should hear about lockouts, "
             "staff access changes and repeated refused downloads.",
        id="banda.W002",
    )]


@register(Tags.security, deploy=True)
def private_file_storage(app_configs, **kwargs):
    """The Privacy Policy says private files are kept in Cloudflare R2 (apps/files/storage.py)."""
    if getattr(settings, "PRIVATE_STORAGE_BACKEND", "local") != "local" or settings.DEBUG:
        return []
    return [Warning(
        "Private files are stored on this server's disk (PRIVATE_STORAGE_BACKEND=local).",
        hint="Set PRIVATE_STORAGE_BACKEND=r2 with the R2_* settings: the Privacy Policy says files are kept "
             "in Cloudflare R2, and a server disk is lost with the server.",
        id="banda.W003",
    )]


@register(Tags.security, deploy=True)
def mfa_encryption_key(app_configs, **kwargs):
    """Authenticator secrets should have their own key (apps/accounts/mfa.py)."""
    if getattr(settings, "MFA_ENCRYPTION_KEY", ""):
        return []
    return [Warning(
        "MFA_ENCRYPTION_KEY is not set, so authenticator secrets are encrypted with a key derived "
        "from DJANGO_SECRET_KEY.",
        hint="Set MFA_ENCRYPTION_KEY to a Fernet key. Then the secret key can be rotated after a leak "
             "without every staff authenticator stopping working.",
        id="banda.W004",
    )]
