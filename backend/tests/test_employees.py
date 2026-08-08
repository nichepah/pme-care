"""Employee search, amendment, retirement and history (SRCH-1, HT-1, EMP-2)."""

from app.models import UserRole


def test_list_is_paginated_and_searchable(client, make_user, register, auth):
    """SRCH-1: results come back in the standard envelope and ``q`` matches
    both name and personal number, anywhere in the value."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-l1")
    register(client, ht, "PN-1", full_name="Asha Verma")
    register(client, ht, "PN-2", full_name="Bhaskar Rao")
    register(client, ht, "XX-9", full_name="Chitra Nair")

    everyone = client.get("/api/v1/employees", headers=auth(ht)).json()
    assert everyone["total"] == 3
    assert everyone["page"] == 1 and everyone["size"] == 20
    assert [e["full_name"] for e in everyone["items"]] == ["Asha Verma", "Bhaskar Rao", "Chitra Nair"]

    by_name = client.get("/api/v1/employees?q=haskar", headers=auth(ht)).json()
    assert [e["personal_number"] for e in by_name["items"]] == ["PN-2"]

    by_number = client.get("/api/v1/employees?q=PN-", headers=auth(ht)).json()
    assert by_number["total"] == 2


def test_list_paging_walks_the_whole_set(client, make_user, register, auth):
    """``total`` counts every match, not just the page, so a client can page."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-l2")
    for i in range(5):
        register(client, ht, f"P-{i}", full_name=f"Employee {i}")

    first = client.get("/api/v1/employees?size=2", headers=auth(ht)).json()
    assert first["total"] == 5 and len(first["items"]) == 2

    third = client.get("/api/v1/employees?size=2&page=3", headers=auth(ht)).json()
    assert third["total"] == 5 and len(third["items"]) == 1

    seen = {e["id"] for e in first["items"]} | {e["id"] for e in third["items"]}
    assert len(seen) == 3, "pages must not repeat rows"


def test_list_rejects_oversized_page(client, make_user, auth):
    """Size is capped so one request cannot ask for the whole table."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-l3")
    r = client.get("/api/v1/employees?size=5000", headers=auth(ht))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_employee_role_cannot_list(client, make_user, auth):
    """AUTH-4: the population list is staff-only."""
    emp = make_user(role=UserRole.EMPLOYEE, uid="emp-l1")
    assert client.get("/api/v1/employees", headers=auth(emp)).status_code == 403


def test_patch_changes_only_the_fields_sent(client, make_user, register, auth):
    """HT-1: a partial update leaves everything it did not mention alone."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-p1")
    employee = register(client, ht, "P-PATCH", department="Foundry", plant="Plant 2")

    r = client.patch(f"/api/v1/employees/{employee['id']}", headers=auth(ht),
                     json={"department": "Rolling Mill"})
    assert r.status_code == 200, r.text
    assert r.json()["department"] == "Rolling Mill"
    assert r.json()["plant"] == "Plant 2"
    assert r.json()["full_name"] == employee["full_name"]


def test_patch_rejects_invalid_email(client, make_user, register, auth):
    """A malformed address is a 400, not a stored typo."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-p2")
    employee = register(client, ht, "P-MAIL")
    r = client.patch(f"/api/v1/employees/{employee['id']}", headers=auth(ht),
                     json={"email": "not-an-address"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_requires_health_team_or_admin(client, make_user, register, auth):
    """AUTH-4: a Doctor cannot amend employee master data."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-p3")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-p3")
    employee = register(client, ht, "P-P3")
    r = client.patch(f"/api/v1/employees/{employee['id']}", headers=auth(doc),
                     json={"department": "X"})
    assert r.status_code == 403


def test_retired_employee_drops_out_of_the_default_list(client, make_user, register, auth):
    """is_active=false retires someone without losing the record."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-r1")
    employee = register(client, ht, "P-RET")
    client.patch(f"/api/v1/employees/{employee['id']}", headers=auth(ht), json={"is_active": False})

    assert client.get("/api/v1/employees", headers=auth(ht)).json()["total"] == 0
    retired = client.get("/api/v1/employees?is_active=false", headers=auth(ht)).json()
    assert [e["id"] for e in retired["items"]] == [employee["id"]]
    # Still reachable by id — the history has to stay auditable.
    assert client.get(f"/api/v1/employees/{employee['id']}", headers=auth(ht)).status_code == 200


def test_soft_delete_hides_the_employee_and_frees_the_number(client, make_user, register, auth):
    """Deleting is a soft delete, and the personal number becomes reusable
    because the unique index only covers live rows."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-d1")
    employee = register(client, admin, "P-DEL")

    assert client.delete(f"/api/v1/employees/{employee['id']}", headers=auth(admin)).status_code == 204
    assert client.get(f"/api/v1/employees/{employee['id']}", headers=auth(admin)).status_code == 404
    assert client.get("/api/v1/employees?is_active=false", headers=auth(admin)).json()["total"] == 0

    reused = register(client, admin, "P-DEL", full_name="Somebody New")
    assert reused["id"] != employee["id"]


def test_soft_delete_requires_admin(client, make_user, register, auth):
    """Health Team can retire an employee but not delete one."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-d2")
    employee = register(client, ht, "P-DEL2")
    assert client.delete(f"/api/v1/employees/{employee['id']}", headers=auth(ht)).status_code == 403


def test_soft_delete_refused_while_an_examination_is_open(client, make_user, register, auth):
    """An open PME must be cancelled first, so it can never be orphaned."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-d3")
    employee = register(client, admin, "P-DEL3")
    sched = client.post("/api/v1/examinations", headers=auth(admin),
                        json={"employee_id": employee["id"], "scheduled_date": "2026-09-01"})
    assert sched.status_code == 201

    blocked = client.delete(f"/api/v1/employees/{employee['id']}", headers=auth(admin))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CONFLICT"

    client.post(f"/api/v1/examinations/{sched.json()['id']}/cancel", headers=auth(admin),
                json={"reason": "Employee left the company."})
    assert client.delete(f"/api/v1/employees/{employee['id']}", headers=auth(admin)).status_code == 204


def test_soft_delete_revokes_the_linked_login(client, make_user, register, auth):
    """After deletion the employee's own token must stop working (AUTH-7)."""
    admin = make_user(role=UserRole.ADMIN, uid="adm-d4")
    employee = register(client, admin, "P-DEL4")
    token = client.post(f"/api/v1/employees/{employee['id']}/login",
                        headers=auth(admin)).json()["dev_bearer_token"]
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 200

    client.delete(f"/api/v1/employees/{employee['id']}", headers=auth(admin))
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 401


def test_examination_history_is_scoped_to_the_owner(client, make_user, register, auth):
    """EMP-2: an employee sees their own history and nobody else's."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-h1")
    mine = register(client, ht, "P-H1")
    theirs = register(client, ht, "P-H2")
    token = client.post(f"/api/v1/employees/{mine['id']}/login",
                        headers=auth(ht)).json()["dev_bearer_token"]

    sched = client.post("/api/v1/examinations", headers=auth(ht),
                        json={"employee_id": mine["id"], "scheduled_date": "2026-09-01"})
    assert sched.status_code == 201

    own = client.get(f"/api/v1/employees/{mine['id']}/examinations", headers=auth(token))
    assert own.status_code == 200
    assert own.json()["total"] == 1

    other = client.get(f"/api/v1/employees/{theirs['id']}/examinations", headers=auth(token))
    assert other.status_code == 404


def test_examination_history_is_newest_first(client, make_user, register, auth):
    """History reads as a timeline, most recent examination first."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-h2")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-h2")
    employee = register(client, ht, "P-H3")

    for day in ("2026-03-01", "2026-06-01", "2026-09-01"):
        exam = client.post("/api/v1/examinations", headers=auth(ht),
                           json={"employee_id": employee["id"], "scheduled_date": day}).json()
        client.post(f"/api/v1/examinations/{exam['id']}/complete", headers=auth(doc),
                    json={"fitness_status": "FIT"})

    history = client.get(f"/api/v1/employees/{employee['id']}/examinations", headers=auth(ht)).json()
    assert [x["scheduled_date"] for x in history["items"]] == ["2026-09-01", "2026-06-01", "2026-03-01"]
