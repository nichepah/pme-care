"""Core-module tests; each maps to a requirement ID from the SRS."""

from fastapi.testclient import TestClient

from app.models import UserRole


def test_health_is_public(client):
    """/health responds without credentials (warm-up probe)."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_liveness_does_not_depend_on_the_database(client, monkeypatch):
    """A database outage must not be read as "kill this container".

    Restarting every instance during a Neon blip would turn a brief dependency
    failure into an outage, and the replacements could not reach the database
    either. Readiness reacts to the database; liveness only to the process.
    """
    from app.main import create_app

    def unreachable(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr("app.routes.system.text", unreachable)
    # raise_server_exceptions=False so the error handler's response is returned
    # rather than re-raised, which is what a real probe would see.
    with TestClient(create_app(), raise_server_exceptions=False) as probe:
        assert probe.get("/api/v1/health").status_code == 500    # readiness fails...
        live = probe.get("/api/v1/health/live")                  # ...liveness does not
    assert live.status_code == 200
    assert live.json()["status"] == "alive"


def test_me_requires_token(client):
    """AUTH-2: missing credentials → 401 with the standard envelope."""
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "UNAUTHENTICATED"
    assert body["request_id"].startswith("req_")


def test_me_returns_identity(client, make_user):
    """AUTH-1/AUTH-4: valid token resolves to the DB user and role."""
    token = make_user(role=UserRole.DOCTOR, uid="doc-1")
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["role"] == "DOCTOR"
    assert r.json()["email"] == "doc-1@example.com"


def test_unknown_uid_rejected(client):
    """AUTH-4: valid token with no application account → 401."""
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer ghost"})
    assert r.status_code == 401


def test_inactive_user_rejected(client, make_user):
    """AUTH-7: deactivated accounts cannot authenticate."""
    token = make_user(uid="gone", active=False)
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_security_headers_present(client):
    """SEC-3/SEC-4: hardening headers stamped on every response."""
    r = client.get("/api/v1/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "X-Request-ID" in r.headers


def test_request_id_is_echoed_back(client):
    """A caller-supplied request id is reused, so a client-side log line and a
    server-side one can be lined up."""
    r = client.get("/api/v1/health", headers={"X-Request-ID": "req_from-client"})
    assert r.headers["X-Request-ID"] == "req_from-client"


def test_api_surface_is_hidden_in_production(monkeypatch):
    """The schema enumerates every route and field, so production serves
    neither it nor the docs page — hiding only the UI would hide nothing."""
    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "ENV", "production")
    with TestClient(create_app()) as production_client:
        assert production_client.get("/docs").status_code == 404
        assert production_client.get("/openapi.json").status_code == 404
        assert production_client.get("/api/v1/health").status_code == 200
