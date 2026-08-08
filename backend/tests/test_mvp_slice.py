"""Tests for the MVP vertical slice: register -> schedule -> complete -> view.

Each test maps to a requirement ID; the last test walks the full happy path
end to end, mirroring the real Health Team -> Doctor -> Employee flow.
"""

import datetime as dt

from app.models import UserRole


def _create_employee(client, token, personal_number="P100"):
    """Helper: register one employee as Health Team, return its response body."""
    r = client.post("/api/v1/employees",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"personal_number": personal_number, "full_name": "R. Kumar",
                          "department": "Foundry", "plant": "Plant 2",
                          "contact_number": "9990001111"})
    assert r.status_code == 201, r.text
    return r.json()


def test_employee_creation_requires_health_team_or_admin(client, make_user):
    """AUTH-4/HT-1: a Doctor cannot register employees."""
    token = make_user(role=UserRole.DOCTOR, uid="doc-x")
    r = client.post("/api/v1/employees", headers={"Authorization": f"Bearer {token}"},
                    json={"personal_number": "P1", "full_name": "X", "department": "D",
                          "plant": "P", "contact_number": "111"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_duplicate_personal_number_conflicts(client, make_user):
    """HT-1: registering the same personal number twice returns 409."""
    token = make_user(role=UserRole.HEALTH_TEAM, uid="ht-1")
    _create_employee(client, token, "P200")
    r = client.post("/api/v1/employees", headers={"Authorization": f"Bearer {token}"},
                    json={"personal_number": "P200", "full_name": "Someone Else",
                          "department": "D", "plant": "P", "contact_number": "222"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_cannot_double_schedule_open_examination(client, make_user):
    """HT-2: scheduling a second exam while one is open returns 409."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-2")
    emp = _create_employee(client, ht, "P300")
    headers = {"Authorization": f"Bearer {ht}"}
    r1 = client.post("/api/v1/examinations", headers=headers,
                     json={"employee_id": emp["id"], "scheduled_date": "2026-09-01"})
    assert r1.status_code == 201
    r2 = client.post("/api/v1/examinations", headers=headers,
                     json={"employee_id": emp["id"], "scheduled_date": "2026-09-15"})
    assert r2.status_code == 409


def test_unfit_decision_requires_remarks(client, make_user):
    """DOC-6 (simplified): UNFIT without remarks is a business-rule violation."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-3")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-3")
    emp = _create_employee(client, ht, "P400")
    sched = client.post("/api/v1/examinations", headers={"Authorization": f"Bearer {ht}"},
                        json={"employee_id": emp["id"], "scheduled_date": "2026-09-01"})
    exam_id = sched.json()["id"]

    r = client.post(f"/api/v1/examinations/{exam_id}/complete",
                    headers={"Authorization": f"Bearer {doc}"},
                    json={"fitness_status": "UNFIT"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"


def test_employee_cannot_view_another_employees_record(client, make_user):
    """EMP-5/anti-enumeration: an employee gets 404, not 403, for another id."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-4")
    other = _create_employee(client, ht, "P500")
    emp_user = make_user(role=UserRole.EMPLOYEE, uid="emp-4")

    r = client.get(f"/api/v1/employees/{other['id']}",
                   headers={"Authorization": f"Bearer {emp_user}"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_create_login_links_employee_and_returns_working_token(client, make_user):
    """Closes the register->login gap: a freshly registered employee can be
    given a login, and that login can then view its own status (EMP-5)."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-6")
    employee = _create_employee(client, ht, "P700")

    r = client.post(f"/api/v1/employees/{employee['id']}/login",
                    headers={"Authorization": f"Bearer {ht}"})
    assert r.status_code == 201
    token = r.json()["dev_bearer_token"]

    status = client.get(f"/api/v1/employees/{employee['id']}",
                        headers={"Authorization": f"Bearer {token}"})
    assert status.status_code == 200
    assert status.json()["personal_number"] == "P700"


def test_create_login_twice_conflicts(client, make_user):
    """An employee cannot be given a second login once one already exists."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-7")
    employee = _create_employee(client, ht, "P800")
    headers = {"Authorization": f"Bearer {ht}"}

    first = client.post(f"/api/v1/employees/{employee['id']}/login", headers=headers)
    assert first.status_code == 201
    second = client.post(f"/api/v1/employees/{employee['id']}/login", headers=headers)
    assert second.status_code == 409


def test_create_login_requires_health_team_or_admin(client, make_user):
    """A Doctor cannot create employee logins."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-8")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-8")
    employee = _create_employee(client, ht, "P900")

    r = client.post(f"/api/v1/employees/{employee['id']}/login",
                    headers={"Authorization": f"Bearer {doc}"})
    assert r.status_code == 403


def test_full_happy_path_register_schedule_complete_view(client, make_user):
    """The real end-to-end flow: HT registers -> HT creates a login for them
    -> HT schedules -> Doctor completes with FIT -> the employee's own login
    sees their own status.
    """
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-5")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-5")

    employee = _create_employee(client, ht, "P600")
    ht_headers = {"Authorization": f"Bearer {ht}"}

    login = client.post(f"/api/v1/employees/{employee['id']}/login", headers=ht_headers)
    assert login.status_code == 201
    emp_token = login.json()["dev_bearer_token"]

    sched = client.post("/api/v1/examinations", headers=ht_headers,
                        json={"employee_id": employee["id"], "scheduled_date": str(dt.date.today())})
    assert sched.status_code == 201
    exam_id = sched.json()["id"]

    completed = client.post(f"/api/v1/examinations/{exam_id}/complete",
                            headers={"Authorization": f"Bearer {doc}"},
                            json={"fitness_status": "FIT", "bp_systolic": 120,
                                  "bp_diastolic": 80, "height_cm": 172, "weight_kg": 70,
                                  "remarks": "All parameters normal."})
    assert completed.status_code == 200
    assert completed.json()["fitness_status"] == "FIT"

    status = client.get(f"/api/v1/employees/{employee['id']}",
                        headers={"Authorization": f"Bearer {emp_token}"})
    assert status.status_code == 200
    body = status.json()
    assert body["latest_examination"]["fitness_status"] == "FIT"
    assert body["latest_examination"]["status"] == "COMPLETED"
