"""Audit trail (AUD-1, AUD-2, SEC-8)."""

from app.models import UserRole


def test_audit_trail_records_the_whole_flow(client, make_user, register, auth):
    """AUD-1: every change leaves a row, attributed to the actor who made it."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-a1")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-a1")
    employee = register(client, admin, "P-A1")
    exam = client.post("/api/v1/examinations", headers=auth(admin),
                       json={"employee_id": employee["id"], "scheduled_date": "2026-09-01"}).json()
    client.post(f"/api/v1/examinations/{exam['id']}/complete", headers=auth(doc),
                json={"fitness_status": "FIT"})

    trail = client.get("/api/v1/audit-logs", headers=auth(admin)).json()
    actions = {(row["entity_type"], row["action"]) for row in trail["items"]}
    assert ("employee", "CREATE") in actions
    assert ("examination", "CREATE") in actions
    assert ("examination", "UPDATE") in actions
    # Newest first.
    assert trail["items"][0]["entity_type"] == "examination"


def test_audit_trail_can_be_filtered_to_one_record(client, make_user, register, auth):
    """The common question is "what happened to this employee?"."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-a2")
    subject = register(client, admin, "P-A2")
    register(client, admin, "P-A3")
    client.patch(f"/api/v1/employees/{subject['id']}", headers=auth(admin),
                 json={"department": "Rolling Mill"})

    scoped = client.get(f"/api/v1/audit-logs?entity_type=employee&entity_id={subject['id']}",
                        headers=auth(admin)).json()
    assert scoped["total"] == 2
    assert {row["action"] for row in scoped["items"]} == {"CREATE", "UPDATE"}
    assert all(row["entity_id"] == subject["id"] for row in scoped["items"])


def test_audit_summary_names_fields_without_leaking_values(client, make_user, register, auth):
    """SEC-8: the summary carries field NAMES only — never clinical values."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-a3")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-a3")
    employee = register(client, admin, "P-A4")
    exam = client.post("/api/v1/examinations", headers=auth(admin),
                       json={"employee_id": employee["id"], "scheduled_date": "2026-09-01"}).json()
    client.post(f"/api/v1/examinations/{exam['id']}/complete", headers=auth(doc),
                json={"fitness_status": "UNFIT", "bp_systolic": 190, "bp_diastolic": 115,
                      "remarks": "Severe hypertension, referred to cardiology."})

    trail = client.get("/api/v1/audit-logs?entity_type=examination&action=UPDATE",
                       headers=auth(admin))
    assert "bp_systolic" in trail.text, "the changed field names should be recorded"
    assert "190" not in trail.text
    assert "hypertension" not in trail.text
    assert "UNFIT" not in trail.text


def test_audit_trail_records_the_client_ip(client, make_user, register, auth):
    """The caller's IP is captured and comes back as a string.

    Two things are checked here that only a real IP exercises: Cloud Run's
    ``X-Forwarded-For`` is preferred over the direct peer, and the ``INET``
    column — which psycopg reads back as an ``IPv4Address`` object — is
    converted before it reaches the response model.
    """
    admin = make_user(role=UserRole.ADMIN, uid="adm-a4")
    client.post("/api/v1/employees",
                headers={**auth(admin), "X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
                json={"personal_number": "P-IP", "full_name": "Ip Test", "department": "D",
                      "plant": "P", "contact_number": "1"})

    trail = client.get("/api/v1/audit-logs?entity_type=employee", headers=auth(admin))
    assert trail.status_code == 200, trail.text
    assert trail.json()["items"][0]["ip_address"] == "203.0.113.9"


def test_unparseable_forwarded_ip_is_stored_as_null(client, make_user, auth):
    """A spoofed or malformed header must not make the request fail: the
    ``INET`` column would reject the value, so it becomes NULL instead."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-a7")
    created = client.post("/api/v1/employees",
                          headers={**auth(admin), "X-Forwarded-For": "not-an-ip"},
                          json={"personal_number": "P-IP2", "full_name": "Spoof", "department": "D",
                                "plant": "P", "contact_number": "1"})
    assert created.status_code == 201, created.text

    trail = client.get("/api/v1/audit-logs?entity_type=employee", headers=auth(admin))
    assert trail.json()["items"][0]["ip_address"] is None


def test_audit_trail_is_admin_only(client, make_user, auth):
    """AUD-2: the trail is not readable by the people it records."""
    for role, uid in ((UserRole.DOCTOR, "doc-a5"), (UserRole.HEALTH_TEAM, "ht-a5"),
                      (UserRole.EMPLOYEE, "emp-a5")):
        token = make_user(role=role, uid=uid)
        assert client.get("/api/v1/audit-logs", headers=auth(token)).status_code == 403


def test_audit_trail_is_append_only_over_http(client, make_user, auth):
    """AUD-2: there is no endpoint that edits or removes history."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-a6")
    for method in (client.post, client.patch, client.put, client.delete):
        response = method("/api/v1/audit-logs", headers=auth(admin))
        assert response.status_code == 405, f"{method.__name__} should not be routed"
