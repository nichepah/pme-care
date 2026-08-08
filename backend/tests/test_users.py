"""Account administration (AUTH-4, AUTH-7)."""

from types import SimpleNamespace

from app.models import UserRole


def test_created_account_carries_a_token_in_fake_mode(client, make_user, auth):
    """Locally the uid doubles as the bearer token, so the account is usable at
    once and no sign-in link is needed."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-fake")
    body = client.post("/api/v1/users", headers=auth(admin),
                       json={"email": "fake@example.com", "display_name": "Fake Doc",
                             "role": "DOCTOR"}).json()
    assert body["dev_bearer_token"]
    assert body["sign_in_link"] is None
    assert client.get("/api/v1/me", headers=auth(body["dev_bearer_token"])).status_code == 200


def test_employee_login_requires_an_email_in_production(client, make_user, register, auth,
                                                        monkeypatch):
    """Without an address there is nowhere to send the sign-in link, so the
    employee could never prove who they are.

    Only the flag the route reads is patched: flipping it on the shared settings
    object would also switch token verification to Firebase and fail the request
    as a 401 long before this rule is reached.
    """
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-noemail")
    employee = register(client, ht, "P-NOMAIL")     # registered without an e-mail
    monkeypatch.setattr("app.routes.employees.settings", SimpleNamespace(AUTH_FAKE_MODE=False))

    r = client.post(f"/api/v1/employees/{employee['id']}/login", headers=auth(ht))
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["field"] == "email"


def test_admin_creates_a_working_doctor_account(client, make_user, auth):
    """The provisioned token authenticates and carries the requested role."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-u1")
    r = client.post("/api/v1/users", headers=auth(admin),
                    json={"email": "new.doc@example.com", "display_name": "Dr New",
                          "role": "DOCTOR"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "DOCTOR" and body["is_active"] is True

    me = client.get("/api/v1/me", headers=auth(body["dev_bearer_token"]))
    assert me.status_code == 200
    assert me.json()["role"] == "DOCTOR"


def test_only_admin_can_create_accounts(client, make_user, auth):
    """AUTH-4: provisioning is an Admin power."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-u1")
    r = client.post("/api/v1/users", headers=auth(ht),
                    json={"email": "x@example.com", "display_name": "X", "role": "DOCTOR"})
    assert r.status_code == 403


def test_employee_accounts_cannot_be_created_here(client, make_user, auth):
    """An EMPLOYEE login must stay tied to an employee record, so it comes
    from POST /employees/{id}/login instead."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-u2")
    r = client.post("/api/v1/users", headers=auth(admin),
                    json={"email": "e@example.com", "display_name": "E", "role": "EMPLOYEE"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_duplicate_email_conflicts(client, make_user, auth):
    """One live account per address."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-u3")
    payload = {"email": "dup@example.com", "display_name": "First", "role": "DOCTOR"}
    assert client.post("/api/v1/users", headers=auth(admin), json=payload).status_code == 201
    second = client.post("/api/v1/users", headers=auth(admin),
                         json={**payload, "display_name": "Second"})
    assert second.status_code == 409


def test_deactivation_revokes_access_immediately(client, make_user, auth):
    """AUTH-7: a deactivated account stops authenticating on the next call."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-u4")
    created = client.post("/api/v1/users", headers=auth(admin),
                          json={"email": "temp@example.com", "display_name": "Temp",
                                "role": "HEALTH_TEAM"}).json()
    token = created["dev_bearer_token"]
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 200

    r = client.patch(f"/api/v1/users/{created['id']}", headers=auth(admin),
                     json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 401


def test_soft_deleted_account_cannot_authenticate(client, make_user, auth):
    """Deleting an account revokes it too, while keeping the row its audit
    rows point at."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-u5")
    created = client.post("/api/v1/users", headers=auth(admin),
                          json={"email": "gone@example.com", "display_name": "Gone",
                                "role": "DOCTOR"}).json()

    assert client.delete(f"/api/v1/users/{created['id']}", headers=auth(admin)).status_code == 204
    assert client.get("/api/v1/me", headers=auth(created["dev_bearer_token"])).status_code == 401
    assert client.get(f"/api/v1/users/{created['id']}", headers=auth(admin)).status_code == 404


def test_admin_cannot_lock_itself_out(client, make_user, auth, db_session):
    """Self-deactivation and self-demotion are refused: that is how a
    deployment ends up with no working administrator."""
    from sqlalchemy import select

    from app.models import User

    admin = make_user(role=UserRole.ADMIN, uid="adm-u6")
    own_id = db_session.execute(select(User).where(User.firebase_uid == admin)).scalar_one().id

    assert client.patch(f"/api/v1/users/{own_id}", headers=auth(admin),
                        json={"is_active": False}).status_code == 403
    assert client.patch(f"/api/v1/users/{own_id}", headers=auth(admin),
                        json={"role": "DOCTOR"}).status_code == 403
    assert client.delete(f"/api/v1/users/{own_id}", headers=auth(admin)).status_code == 403
    # Renaming yourself is fine — it locks nobody out.
    assert client.patch(f"/api/v1/users/{own_id}", headers=auth(admin),
                        json={"display_name": "The Admin"}).status_code == 200


def test_employee_role_cannot_be_reassigned(client, make_user, register, auth):
    """An employee login's role is implied by its linked employee record."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-u7")
    employee = register(client, admin, "P-U7")
    login = client.post(f"/api/v1/employees/{employee['id']}/login", headers=auth(admin)).json()

    r = client.patch(f"/api/v1/users/{login['user_id']}", headers=auth(admin),
                     json={"role": "DOCTOR"})
    assert r.status_code == 403


def test_user_list_filters_by_role(client, make_user, auth):
    """Admin can see who holds which role."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-u8")
    make_user(role=UserRole.DOCTOR, uid="doc-u8")
    make_user(role=UserRole.DOCTOR, uid="doc-u8b")
    make_user(role=UserRole.HEALTH_TEAM, uid="ht-u8")

    doctors = client.get("/api/v1/users?role=DOCTOR", headers=auth(admin)).json()
    assert doctors["total"] == 2
    everyone = client.get("/api/v1/users", headers=auth(admin)).json()
    assert everyone["total"] == 4


def test_user_list_never_exposes_the_firebase_uid(client, make_user, auth):
    """The uid is a credential in AUTH_FAKE_MODE and an internal key otherwise,
    so it must not be part of the account representation."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-u9")
    listed = client.get("/api/v1/users", headers=auth(admin)).json()
    assert set(listed["items"][0]) == {"id", "email", "display_name", "role",
                                       "is_active", "last_login_at"}


def test_me_records_the_login(client, make_user, auth, db_session):
    """AUTH-1/AUD-1: the first authenticated call after a sign-in stamps
    last_login_at and leaves a LOGIN audit row."""
    from sqlalchemy import select

    from app.models import AuditLog, User

    doc = make_user(role=UserRole.DOCTOR, uid="doc-login")
    assert client.get("/api/v1/me", headers=auth(doc)).status_code == 200

    account = db_session.execute(select(User).where(User.firebase_uid == doc)).scalar_one()
    assert account.last_login_at is not None
    logins = db_session.execute(select(AuditLog).where(AuditLog.action == "LOGIN")).scalars().all()
    assert len(logins) == 1
    assert logins[0].actor_user_id == account.id

    # A second call inside the session window must not add another row.
    client.get("/api/v1/me", headers=auth(doc))
    db_session.expire_all()
    again = db_session.execute(select(AuditLog).where(AuditLog.action == "LOGIN")).scalars().all()
    assert len(again) == 1
