"""Account provisioning in both modes, and the first-admin bootstrap.

The production path is exercised by stubbing the Firebase Admin SDK's functions
on the real module — so the code under test is the code that ships, and the only
thing faked is the network call to Google. Without this, everything behind
``AUTH_FAKE_MODE=false`` would be untested until the day it was deployed.
"""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import User, UserRole
from app.provisioning import (
    FakeProvisioner,
    FirebaseProvisioner,
    ProvisioningError,
    get_provisioner,
)
from scripts.bootstrap_admin import create_first_admin


def _production_settings() -> SimpleNamespace:
    """Stand-in settings with the flag off — carrying every field the code reads."""
    return SimpleNamespace(AUTH_FAKE_MODE=False,
                           SIGN_IN_CONTINUE_URL="https://pme-care.example/")


@pytest.fixture()
def firebase(monkeypatch):
    """Stub the Admin SDK calls and record what the code asked it to do."""
    from firebase_admin import auth as fb_auth

    calls = SimpleNamespace(created=[], links=[], disabled=[], revoked=[])

    def create_user(**kwargs):
        calls.created.append(kwargs)
        return SimpleNamespace(uid="firebase-uid-abc123")

    def generate_link(email, settings_obj):
        calls.links.append((email, settings_obj.url))
        return "https://pme-care.example/signin?oob=xyz"

    monkeypatch.setattr(fb_auth, "create_user", create_user)
    monkeypatch.setattr(fb_auth, "generate_sign_in_with_email_link", generate_link)
    monkeypatch.setattr(fb_auth, "update_user", lambda uid, **kw: calls.disabled.append(uid))
    monkeypatch.setattr(fb_auth, "revoke_refresh_tokens", lambda uid: calls.revoked.append(uid))
    return calls


# --- which implementation is chosen ----------------------------------------

def test_fake_mode_selects_the_synthetic_provisioner():
    """Tests and local development must never reach out to Firebase."""
    assert settings.AUTH_FAKE_MODE is True
    assert isinstance(get_provisioner(), FakeProvisioner)


def test_production_selects_the_firebase_provisioner(monkeypatch):
    """With the flag off, the real SDK is used."""
    monkeypatch.setattr("app.provisioning.settings", _production_settings())
    assert isinstance(get_provisioner(), FirebaseProvisioner)


def test_fake_uids_are_readable_and_unique(db_session, make_user):
    """A uid pasted into the demo UI should be recognisable, and a name clash
    must not produce a duplicate."""
    first = FakeProvisioner("doctor").create(db_session, "a@example.com", "Dr Rao")
    assert first.firebase_uid == "doctor-dr-rao"
    assert first.dev_bearer_token == first.firebase_uid

    make_user(role=UserRole.DOCTOR, uid="doctor-dr-rao")
    second = FakeProvisioner("doctor").create(db_session, "b@example.com", "Dr Rao")
    assert second.firebase_uid.startswith("doctor-dr-rao-")
    assert second.firebase_uid != first.firebase_uid


# --- the real Firebase path -------------------------------------------------

def test_firebase_account_is_created_without_a_password(db_session, firebase):
    """The service must never handle a credential: the account is created
    unverified with no password, and the user sets their own via the link."""
    result = FirebaseProvisioner().create(db_session, "new@example.com", "New Person")

    assert result.firebase_uid == "firebase-uid-abc123"
    assert result.dev_bearer_token is None          # nothing to authenticate with here
    assert result.sign_in_link == "https://pme-care.example/signin?oob=xyz"

    (created,) = firebase.created
    assert created == {"email": "new@example.com", "display_name": "New Person",
                       "email_verified": False}
    assert "password" not in created


def test_existing_firebase_account_is_a_502_not_a_500(db_session, monkeypatch):
    """An address already in Firebase is the provider's refusal, not our fault,
    and the message should say what to do about it."""
    from firebase_admin import auth as fb_auth

    def boom(**kwargs):
        raise fb_auth.EmailAlreadyExistsError("exists", None, None)

    monkeypatch.setattr(fb_auth, "create_user", boom)
    with pytest.raises(ProvisioningError) as caught:
        FirebaseProvisioner().create(db_session, "taken@example.com", "Taken")
    assert caught.value.status_code == 502
    assert "already exists" in caught.value.message


def test_provider_outage_is_a_502(db_session, monkeypatch):
    """Any other SDK failure is also the provider's, so the caller can retry."""
    from firebase_admin import auth as fb_auth

    def boom(**kwargs):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(fb_auth, "create_user", boom)
    with pytest.raises(ProvisioningError) as caught:
        FirebaseProvisioner().create(db_session, "x@example.com", "X")
    assert caught.value.status_code == 502


def test_link_failure_does_not_discard_the_created_account(db_session, monkeypatch, firebase):
    """If the account exists but the link call fails, failing the request would
    orphan a Firebase user. The account is returned without a link instead."""
    from firebase_admin import auth as fb_auth

    def no_link(email, settings_obj):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(fb_auth, "generate_sign_in_with_email_link", no_link)
    result = FirebaseProvisioner().create(db_session, "y@example.com", "Y")
    assert result.firebase_uid == "firebase-uid-abc123"
    assert result.sign_in_link is None


def test_revoke_disables_the_identity_and_its_sessions(firebase):
    """Deactivating our row blocks this API; a live Firebase session could still
    mint tokens for anything else sharing the project."""
    FirebaseProvisioner().revoke("firebase-uid-abc123")
    assert firebase.disabled == ["firebase-uid-abc123"]
    assert firebase.revoked == ["firebase-uid-abc123"]


def test_revoke_survives_a_provider_failure(monkeypatch):
    """Local deactivation has already succeeded and is what this API enforces,
    so a provider error must not turn a successful delete into a 500."""
    from firebase_admin import auth as fb_auth

    monkeypatch.setattr(fb_auth, "update_user",
                        lambda uid, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    FirebaseProvisioner().revoke("whatever")   # must not raise


# --- the endpoint, in production mode --------------------------------------

def test_post_users_returns_a_sign_in_link_in_production(client, make_user, auth, monkeypatch,
                                                         firebase):
    """The endpoint that used to refuse outright now provisions for real."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-prod2")
    monkeypatch.setattr("app.provisioning.settings", _production_settings())

    r = client.post("/api/v1/users", headers=auth(admin),
                    json={"email": "prod.doc@example.com", "display_name": "Prod Doc",
                          "role": "DOCTOR"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sign_in_link"] == "https://pme-care.example/signin?oob=xyz"
    assert body["dev_bearer_token"] is None
    assert firebase.created[0]["email"] == "prod.doc@example.com"


def test_provider_refusal_surfaces_as_502_through_the_api(client, make_user, auth, monkeypatch):
    """A provider error reaches the client in the standard envelope."""
    from firebase_admin import auth as fb_auth

    admin = make_user(role=UserRole.ADMIN, uid="adm-prod3")
    monkeypatch.setattr("app.provisioning.settings", _production_settings())
    monkeypatch.setattr(fb_auth, "create_user",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))

    r = client.post("/api/v1/users", headers=auth(admin),
                    json={"email": "z@example.com", "display_name": "Z", "role": "DOCTOR"})
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "IDENTITY_PROVIDER_ERROR"


# --- bootstrap --------------------------------------------------------------

def test_bootstrap_creates_the_first_admin(db_session, capsys):
    """The deadlock this resolves: every creation endpoint needs an ADMIN, and
    an empty database has none."""
    from sqlalchemy import select

    assert create_first_admin("ops@example.com", "Ops Admin") == 0
    admin = db_session.execute(select(User).where(User.email == "ops@example.com")).scalar_one()
    assert admin.role == UserRole.ADMIN
    assert admin.is_active is True
    assert "bearer token" in capsys.readouterr().out


def test_bootstrap_refuses_when_an_admin_already_exists(make_user, capsys):
    """It is a bootstrap, not a back door for minting extra administrators."""
    make_user(role=UserRole.ADMIN, uid="adm-existing")
    assert create_first_admin("second@example.com", "Second Admin") == 1
    assert "already exists" in capsys.readouterr().err


def test_bootstrap_refuses_a_taken_email(make_user, capsys):
    """Reusing an address would collide with the live-email unique index."""
    from app import db as db_module

    with db_module.SessionLocal() as s:
        s.add(User(firebase_uid="doc-taken", email="taken@example.com",
                   role=UserRole.DOCTOR, display_name="Doctor"))
        s.commit()
    assert create_first_admin("taken@example.com", "Wants To Be Admin") == 1
    assert "already registered" in capsys.readouterr().err


def test_bootstrap_records_an_audit_row_with_no_actor(db_session):
    """It runs outside any request, by an operator with database access, so the
    trail records the act with a null actor rather than inventing one (AUD-1)."""
    from sqlalchemy import select

    from app.models import AuditLog

    assert create_first_admin("audited@example.com", "Audited Admin") == 0
    row = db_session.execute(select(AuditLog).where(AuditLog.action == "CREATE",
                                                    AuditLog.entity_type == "user")).scalar_one()
    assert row.actor_user_id is None
    assert row.summary == {"role": "ADMIN", "via": "bootstrap_admin"}
