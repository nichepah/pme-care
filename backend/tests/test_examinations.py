"""Examination scheduling, cancellation, completion and scope (HT-2, HT-4,
DOC-1, DOC-3, DOC-6)."""

from app.models import UserRole


def _schedule(client, token, employee_id, when="2026-09-01", **extra):
    """Helper: schedule a PME and return the raw response."""
    payload = {"employee_id": employee_id, "scheduled_date": when, **extra}
    return client.post("/api/v1/examinations",
                       headers={"Authorization": f"Bearer {token}"}, json=payload)


def test_schedule_rejects_a_malformed_doctor_id(client, make_user, register, auth):
    """A bad doctor_user_id is a 422 naming the field — never a 500 from a
    foreign-key violation deeper down."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-s1")
    employee = register(client, ht, "P-S1")

    r = _schedule(client, ht, employee["id"], doctor_user_id="not-a-uuid")
    assert r.status_code == 422, r.text
    body = r.json()["error"]
    assert body["code"] == "BUSINESS_RULE_VIOLATION"
    assert body["details"][0]["field"] == "doctor_user_id"


def test_schedule_rejects_an_unknown_doctor(client, make_user, register, auth):
    """A well-formed id that is not an active DOCTOR is refused up front."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-s2")
    employee = register(client, ht, "P-S2")

    missing = _schedule(client, ht, employee["id"],
                        doctor_user_id="00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 422


def test_schedule_rejects_a_non_doctor_as_doctor(client, make_user, register, auth, db_session):
    """Assigning the Health Team user as the examining doctor is refused."""
    from sqlalchemy import select

    from app.models import User

    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-s3")
    employee = register(client, ht, "P-S3")
    ht_user_id = db_session.execute(select(User).where(User.firebase_uid == ht)).scalar_one().id

    r = _schedule(client, ht, employee["id"], doctor_user_id=str(ht_user_id))
    assert r.status_code == 422
    assert "DOCTOR" in r.json()["error"]["message"]


def test_schedule_accepts_a_real_doctor(client, make_user, register, auth, db_session):
    """The happy path for pre-assigning a doctor."""
    from sqlalchemy import select

    from app.models import User

    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-s4")
    make_user(role=UserRole.DOCTOR, uid="doc-s4")
    employee = register(client, ht, "P-S4")
    doctor_id = db_session.execute(select(User).where(User.firebase_uid == "doc-s4")).scalar_one().id

    r = _schedule(client, ht, employee["id"], doctor_user_id=str(doctor_id))
    assert r.status_code == 201, r.text
    assert r.json()["doctor_user_id"] == str(doctor_id)


def test_cannot_schedule_for_a_retired_employee(client, make_user, register, auth):
    """A retired employee is not due for PMEs any more."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-s5")
    employee = register(client, ht, "P-S5")
    client.patch(f"/api/v1/employees/{employee['id']}", headers=auth(ht), json={"is_active": False})

    r = _schedule(client, ht, employee["id"])
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"


def test_cancel_requires_a_reason(client, make_user, register, auth):
    """A cancelled PME is a compliance gap; the reason is not optional."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-c1")
    employee = register(client, ht, "P-C1")
    exam = _schedule(client, ht, employee["id"]).json()

    blank = client.post(f"/api/v1/examinations/{exam['id']}/cancel", headers=auth(ht),
                        json={"reason": ""})
    assert blank.status_code == 400
    missing = client.post(f"/api/v1/examinations/{exam['id']}/cancel", headers=auth(ht), json={})
    assert missing.status_code == 400


def test_cancel_frees_the_employee_to_be_rescheduled(client, make_user, register, auth):
    """The open-examination conflict only counts SCHEDULED rows, so cancelling
    lets the Health Team schedule again."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-c2")
    employee = register(client, ht, "P-C2")
    first = _schedule(client, ht, employee["id"]).json()

    assert _schedule(client, ht, employee["id"], "2026-10-01").status_code == 409

    cancelled = client.post(f"/api/v1/examinations/{first['id']}/cancel", headers=auth(ht),
                            json={"reason": "Plant shutdown."})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancel_reason"] == "Plant shutdown."

    assert _schedule(client, ht, employee["id"], "2026-10-01").status_code == 201


def test_cancel_is_terminal(client, make_user, register, auth):
    """A cancelled examination is never completed or cancelled again."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-c3")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-c3")
    employee = register(client, ht, "P-C3")
    exam = _schedule(client, ht, employee["id"]).json()
    client.post(f"/api/v1/examinations/{exam['id']}/cancel", headers=auth(ht),
                json={"reason": "Duplicate entry."})

    again = client.post(f"/api/v1/examinations/{exam['id']}/cancel", headers=auth(ht),
                        json={"reason": "Oops."})
    assert again.status_code == 422
    completed = client.post(f"/api/v1/examinations/{exam['id']}/complete", headers=auth(doc),
                            json={"fitness_status": "FIT"})
    assert completed.status_code == 422


def test_cancel_requires_health_team_or_admin(client, make_user, register, auth):
    """A Doctor completes examinations; scheduling and cancelling is HT's job."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-c4")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-c4")
    employee = register(client, ht, "P-C4")
    exam = _schedule(client, ht, employee["id"]).json()

    r = client.post(f"/api/v1/examinations/{exam['id']}/cancel", headers=auth(doc),
                    json={"reason": "Not my call."})
    assert r.status_code == 403


def test_temporarily_unfit_also_requires_remarks(client, make_user, register, auth):
    """DOC-6: any outcome other than FIT has to be explained, not just UNFIT."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-t1")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-t1")
    employee = register(client, ht, "P-T1")
    exam = _schedule(client, ht, employee["id"]).json()

    r = client.post(f"/api/v1/examinations/{exam['id']}/complete", headers=auth(doc),
                    json={"fitness_status": "TEMPORARILY_UNFIT"})
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["field"] == "remarks"


def test_complete_rejects_impossible_vitals(client, make_user, register, auth):
    """Out-of-range vitals are a typo, caught before they reach the record."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-t2")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-t2")
    employee = register(client, ht, "P-T2")
    exam = _schedule(client, ht, employee["id"]).json()

    r = client.post(f"/api/v1/examinations/{exam['id']}/complete", headers=auth(doc),
                    json={"fitness_status": "FIT", "bp_systolic": 1200})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_worklist_filters_by_status_and_date_window(client, make_user, register, auth):
    """HT-4/DOC-1: the worklist narrows by status and scheduled-date window."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-w1")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-w1")
    early = register(client, ht, "P-W1")
    late = register(client, ht, "P-W2")
    _schedule(client, ht, early["id"], "2026-03-15")
    later_exam = _schedule(client, ht, late["id"], "2026-11-20").json()
    client.post(f"/api/v1/examinations/{later_exam['id']}/complete", headers=auth(doc),
                json={"fitness_status": "FIT"})

    scheduled = client.get("/api/v1/examinations?status=SCHEDULED", headers=auth(doc)).json()
    assert scheduled["total"] == 1
    assert scheduled["items"][0]["employee_id"] == early["id"]

    window = client.get("/api/v1/examinations?scheduled_from=2026-06-01", headers=auth(doc)).json()
    assert window["total"] == 1
    assert window["items"][0]["id"] == later_exam["id"]

    empty = client.get("/api/v1/examinations?scheduled_to=2026-01-01", headers=auth(doc)).json()
    assert empty["total"] == 0 and empty["items"] == []


def test_examination_detail_is_scoped_to_the_owner(client, make_user, register, auth):
    """An employee can read their own examination and not somebody else's."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-w2")
    mine = register(client, ht, "P-W3")
    theirs = register(client, ht, "P-W4")
    token = client.post(f"/api/v1/employees/{mine['id']}/login",
                        headers=auth(ht)).json()["dev_bearer_token"]

    own = _schedule(client, ht, mine["id"]).json()
    other = _schedule(client, ht, theirs["id"]).json()

    assert client.get(f"/api/v1/examinations/{own['id']}", headers=auth(token)).status_code == 200
    assert client.get(f"/api/v1/examinations/{other['id']}", headers=auth(token)).status_code == 404


def test_unknown_and_malformed_ids_are_both_404(client, make_user, auth):
    """A typo in a URL never becomes a 500 (API spec §1.5)."""
    doc = make_user(role=UserRole.DOCTOR, uid="doc-w3")
    assert client.get("/api/v1/examinations/not-a-uuid", headers=auth(doc)).status_code == 404
    assert client.get("/api/v1/examinations/00000000-0000-0000-0000-000000000000",
                      headers=auth(doc)).status_code == 404
