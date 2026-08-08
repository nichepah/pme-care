"""Due-date arithmetic and the compliance worklist.

This is the part that makes the examinations *periodic*: without it the API
could record a PME but not answer the only question a compliance officer asks —
who has not had one recently enough.
"""

from datetime import date, timedelta

from app.models import FitnessStatus, UserRole
from app.periodicity import add_months, next_due_date

TODAY = date.today()


# --- the arithmetic ---------------------------------------------------------

def test_month_addition_clamps_to_short_months():
    """31 January + 1 month has to land on a date that exists."""
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)   # leap year
    assert add_months(date(2026, 3, 31), 1) == date(2026, 4, 30)


def test_month_addition_crosses_years():
    """A twelve-month cycle keeps the same day, a year later."""
    assert add_months(date(2026, 8, 8), 12) == date(2027, 8, 8)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert add_months(date(2026, 1, 31), 13) == date(2027, 2, 28)


def test_leap_day_examination_recalls_in_a_common_year():
    """29 February + 12 months is 28 February, not an error."""
    assert add_months(date(2028, 2, 29), 12) == date(2029, 2, 28)


def test_fit_starts_a_full_cycle():
    """A clean bill of health is good for the configured year."""
    assert next_due_date(FitnessStatus.FIT, date(2026, 8, 8)) == date(2027, 8, 8)


def test_temporarily_unfit_recalls_much_sooner():
    """A provisional outcome is the point of having a shorter interval."""
    assert next_due_date(FitnessStatus.TEMPORARILY_UNFIT, date(2026, 8, 8)) == date(2026, 11, 8)


def test_unfit_starts_no_cycle():
    """UNFIT is a case to manage, not a booking to make. Inventing a routine
    recall date would quietly downgrade a serious finding."""
    assert next_due_date(FitnessStatus.UNFIT, date(2026, 8, 8)) is None


def test_doctor_recall_date_overrides_the_interval():
    """A clinician who examined the person outranks the schedule — including
    for UNFIT, where they may want a specific review date."""
    recall = date(2026, 9, 1)
    assert next_due_date(FitnessStatus.FIT, date(2026, 8, 8), recall) == recall
    assert next_due_date(FitnessStatus.UNFIT, date(2026, 8, 8), recall) == recall


# --- through the API -------------------------------------------------------

def _complete(client, auth, doc, exam_id, **body):
    """Complete an examination and return the response body."""
    payload = {"fitness_status": "FIT", **body}
    r = client.post(f"/api/v1/examinations/{exam_id}/complete", headers=auth(doc), json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _schedule(client, auth, ht, employee_id, when=str(TODAY)):
    """Schedule a PME and return its id."""
    r = client.post("/api/v1/examinations", headers=auth(ht),
                    json={"employee_id": employee_id, "scheduled_date": when})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_completing_sets_the_next_due_date(client, make_user, register, auth):
    """The due date is counted from when the examination happened, not from when
    it was scheduled, so a late examination does not compound the drift."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due1")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-due1")
    employee = register(client, ht, "P-DUE1")
    exam = _schedule(client, auth, ht, employee["id"], "2026-01-01")

    body = _complete(client, auth, doc, exam)
    assert body["exam_date"] == str(TODAY)
    assert body["next_due_date"] == str(add_months(TODAY, 12))


def test_doctor_can_set_an_explicit_recall_through_the_api(client, make_user, register, auth):
    """The override reaches the stored record."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due2")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-due2")
    employee = register(client, ht, "P-DUE2")
    exam = _schedule(client, auth, ht, employee["id"])

    recall = str(TODAY + timedelta(days=45))
    body = _complete(client, auth, doc, exam, fitness_status="TEMPORARILY_UNFIT",
                     remarks="Recheck BP.", next_due_date=recall)
    assert body["next_due_date"] == recall


def test_never_examined_employee_is_due_immediately(client, make_user, register, auth):
    """Somebody with no examination at all is the most overdue case there is."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due3")
    employee = register(client, ht, "P-DUE3")

    due = client.get("/api/v1/employees/due", headers=auth(ht)).json()
    assert due["total"] == 1
    row = due["items"][0]
    assert row["id"] == employee["id"]
    assert row["never_examined"] is True
    assert row["next_due_date"] is None
    assert row["days_overdue"] is None


def test_scheduling_removes_an_employee_from_the_due_list(client, make_user, register, auth):
    """The list answers "who still needs booking?" — once booked, they are done
    with, and leaving them in would mean booking them twice."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due4")
    employee = register(client, ht, "P-DUE4")
    assert client.get("/api/v1/employees/due", headers=auth(ht)).json()["total"] == 1

    exam = _schedule(client, auth, ht, employee["id"])
    assert client.get("/api/v1/employees/due", headers=auth(ht)).json()["total"] == 0

    # Cancelling puts them back — the PME still has not happened.
    client.post(f"/api/v1/examinations/{exam}/cancel", headers=auth(ht),
                json={"reason": "Employee on leave."})
    assert client.get("/api/v1/employees/due", headers=auth(ht)).json()["total"] == 1


def test_a_fit_employee_drops_out_until_the_cycle_comes_round(client, make_user, register, auth):
    """Completing a PME is what clears someone from the list."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due5")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-due5")
    employee = register(client, ht, "P-DUE5")
    _complete(client, auth, doc, _schedule(client, auth, ht, employee["id"]))

    assert client.get("/api/v1/employees/due", headers=auth(ht)).json()["total"] == 0
    # ...and reappears once the window reaches their due date a year out.
    wide = client.get("/api/v1/employees/due?within_days=365", headers=auth(ht)).json()
    assert wide["total"] == 1
    assert wide["items"][0]["never_examined"] is False


def test_overdue_is_counted_in_days(client, make_user, register, auth, db_session):
    """days_overdue is what a compliance report is actually sorted by."""
    from sqlalchemy import select

    from app.models import Examination

    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due6")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-due6")
    employee = register(client, ht, "P-DUE6")
    exam_id = _schedule(client, auth, ht, employee["id"])
    _complete(client, auth, doc, exam_id)

    # Backdate the due date: the alternative is a test that waits a year.
    exam = db_session.execute(select(Examination)).scalar_one()
    exam.next_due_date = TODAY - timedelta(days=40)
    db_session.commit()

    overdue = client.get("/api/v1/employees/due?overdue_only=true", headers=auth(ht)).json()
    assert overdue["total"] == 1
    assert overdue["items"][0]["days_overdue"] == 40
    assert overdue["items"][0]["last_exam_date"] == str(TODAY)


def test_overdue_only_excludes_the_merely_upcoming(client, make_user, register, auth, db_session):
    """Two different questions: what is late, and what is coming up."""
    from sqlalchemy import select

    from app.models import Employee, Examination

    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due7")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-due7")
    late = register(client, ht, "P-LATE", full_name="Late Person")
    soon = register(client, ht, "P-SOON", full_name="Soon Person")
    for employee in (late, soon):
        _complete(client, auth, doc, _schedule(client, auth, ht, employee["id"]))

    ids = {str(e.id): e.personal_number for e in
           db_session.execute(select(Employee)).scalars().all()}
    for exam in db_session.execute(select(Examination)).scalars().all():
        exam.next_due_date = (TODAY - timedelta(days=10)
                              if ids[str(exam.employee_id)] == "P-LATE"
                              else TODAY + timedelta(days=10))
    db_session.commit()

    both = client.get("/api/v1/employees/due", headers=auth(ht)).json()
    assert both["total"] == 2
    # Most overdue first, so the worklist is already in priority order.
    assert both["items"][0]["personal_number"] == "P-LATE"

    only_late = client.get("/api/v1/employees/due?overdue_only=true", headers=auth(ht)).json()
    assert [r["personal_number"] for r in only_late["items"]] == ["P-LATE"]


def test_unfit_employee_stays_out_of_the_routine_list(client, make_user, register, auth):
    """An UNFIT outcome sets no due date, so the employee does not reappear as a
    routine booking — their situation needs a decision, not a calendar entry."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due8")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-due8")
    employee = register(client, ht, "P-DUE8")
    body = _complete(client, auth, doc, _schedule(client, auth, ht, employee["id"]),
                     fitness_status="UNFIT", remarks="Not fit for this role.")
    assert body["next_due_date"] is None

    assert client.get("/api/v1/employees/due?within_days=365",
                      headers=auth(ht)).json()["total"] == 0


def test_due_list_uses_the_most_recent_examination(client, make_user, register, auth, db_session):
    """With a history, only the latest completed examination sets the due date."""
    from sqlalchemy import select

    from app.models import Examination

    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due9")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-due9")
    employee = register(client, ht, "P-DUE9")

    for _ in range(2):
        _complete(client, auth, doc, _schedule(client, auth, ht, employee["id"]))
    exams = db_session.execute(select(Examination).order_by(Examination.created_at)).scalars().all()
    assert len(exams) == 2
    # Older examination was long overdue; the newer one is not due yet.
    exams[0].exam_date, exams[0].next_due_date = TODAY - timedelta(days=400), TODAY - timedelta(days=35)
    exams[1].exam_date, exams[1].next_due_date = TODAY, TODAY + timedelta(days=330)
    db_session.commit()

    assert client.get("/api/v1/employees/due?overdue_only=true",
                      headers=auth(ht)).json()["total"] == 0


def test_retired_employees_are_never_due(client, make_user, register, auth):
    """Nobody schedules a PME for someone who has left."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due10")
    employee = register(client, ht, "P-DUE10")
    client.patch(f"/api/v1/employees/{employee['id']}", headers=auth(ht), json={"is_active": False})
    assert client.get("/api/v1/employees/due", headers=auth(ht)).json()["total"] == 0


def test_due_list_filters_by_department(client, make_user, register, auth):
    """Plant health teams work their own area."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due11")
    register(client, ht, "P-F1", department="Foundry")
    register(client, ht, "P-R1", department="Rolling Mill")

    foundry = client.get("/api/v1/employees/due?department=Foundry", headers=auth(ht)).json()
    assert [r["personal_number"] for r in foundry["items"]] == ["P-F1"]


def test_due_list_is_staff_only(client, make_user, auth):
    """A compliance list of the whole workforce is not employee-visible."""
    emp = make_user(role=UserRole.EMPLOYEE, uid="emp-due")
    assert client.get("/api/v1/employees/due", headers=auth(emp)).status_code == 403


def test_due_is_not_mistaken_for_an_employee_id(client, make_user, auth):
    """``/employees/due`` must not be routed as ``/employees/{id}``."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-due12")
    r = client.get("/api/v1/employees/due", headers=auth(ht))
    assert r.status_code == 200
    assert "items" in r.json()      # a Page, not an employee detail or a 404
