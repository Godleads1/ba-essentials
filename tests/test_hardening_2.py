"""Follow-ups from checking banda against the vibe-coding risk lists (17 Nov 2026)."""
from __future__ import annotations

import pyotp
import pytest
from cryptography.fernet import Fernet
from django.core.checks import run_checks
from django.test import override_settings

from apps.accounts import mfa
from apps.accounts import services as accounts
from apps.core.exceptions import ValidationFailure

from .conftest import make_user, pdf_upload

NEW_KEY = Fernet.generate_key().decode()


# ---------- authenticator secrets have their own key ----------

def _enrolled(email="mfa.key@example.test", **settings_over):
    user = make_user(email, staff_role="operations")
    secret = pyotp.random_base32()
    user.mfa_secret_encrypted = mfa.encrypt_secret(secret)
    user.mfa_enabled = True
    user.save()
    return user, secret


def _code(secret):
    return pyotp.TOTP(secret).now()


@override_settings(MFA_ENCRYPTION_KEY=NEW_KEY)
def test_new_secrets_use_the_separate_key(db):
    user, secret = _enrolled()
    assert Fernet(NEW_KEY).decrypt(user.mfa_secret_encrypted.encode()).decode() == secret


def test_old_secrets_are_moved_to_the_new_key_on_use_then_survive_a_secret_key_change(db, settings):
    settings.MFA_ENCRYPTION_KEY = ""
    user, secret = _enrolled("legacy@example.test")  # made with the old, secret-key-derived key
    settings.MFA_ENCRYPTION_KEY = NEW_KEY
    assert mfa.needs_rotation(user.mfa_secret_encrypted)
    assert accounts.verify_mfa(user, _code(secret))  # still readable, and re-encrypted now
    user.refresh_from_db()
    assert not mfa.needs_rotation(user.mfa_secret_encrypted)
    # After a leak the secret key is rotated: the authenticator keeps working.
    settings.SECRET_KEY = "a-completely-new-secret-key-after-a-leak-0000000000000000000000"
    user.mfa_last_timestep = None
    user.save()
    assert mfa.decrypt_secret(user.mfa_secret_encrypted) == secret


@override_settings(MFA_ENCRYPTION_KEY="")
def test_deploy_check_warns_without_a_separate_key():
    assert "banda.W004" in [m.id for m in run_checks(include_deployment_checks=True)]


@override_settings(MFA_ENCRYPTION_KEY=NEW_KEY)
def test_deploy_check_quiet_with_the_key():
    assert "banda.W004" not in [m.id for m in run_checks(include_deployment_checks=True)]


# ---------- a business's uploads are capped ----------

def _upload(organization, user, name="brief.pdf"):
    from apps.files import services as files

    return files.store_upload(organization=organization, user=user, uploaded_file=pdf_upload(name))


def test_client_uploads_stop_at_the_business_limit(organization, owner, settings):
    first = _upload(organization, owner)
    settings.ORG_STORAGE_LIMIT_BYTES = first.size_bytes * 2
    _upload(organization, owner, "second.pdf")
    with pytest.raises(ValidationFailure, match="file storage"):
        _upload(organization, owner, "third.pdf")


def test_staff_files_never_count_or_get_refused(organization, owner, ops_user, settings):
    staff_file = _upload(organization, ops_user, "deliverable.pdf")
    settings.ORG_STORAGE_LIMIT_BYTES = staff_file.size_bytes
    _upload(organization, ops_user, "another-deliverable.pdf")  # staff: no cap
    _upload(organization, owner, "client.pdf")  # staff files don't use the client's room


def test_other_businesses_have_their_own_room(organization, other_organization, owner, other_owner, settings):
    first = _upload(organization, owner)
    settings.ORG_STORAGE_LIMIT_BYTES = first.size_bytes
    _upload(other_organization, other_owner, "theirs.pdf")


def test_the_submit_page_shows_the_limit_instead_of_failing(organization, owner, service_version, settings):
    from .conftest import login_client

    settings.ORG_STORAGE_LIMIT_BYTES = 1
    response = login_client(owner).post("/app/tasks/new/", {
        "service_version": str(service_version.pk), "title": "Synthetic request with a file",
        "brief": "A synthetic brief long enough to pass validation for this test.", "priority": "medium",
        "work_country": "NG", "attachments": [pdf_upload()],
    }, follow=True)
    assert "file storage" in response.content.decode()
    from apps.files.models import PrivateFile

    assert not PrivateFile.objects.exists()


# ---------- the database is closed to the internet ----------
# Now checked for the Hostinger setup in tests/test_deployment.py (test_only_caddy_faces_the_internet).
