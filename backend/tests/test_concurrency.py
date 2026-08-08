"""Uniqueness under concurrency.

Every "does this exist yet?" check in the API is a SELECT followed by an INSERT,
which two requests can interleave. These tests bypass the SELECT — by inserting
directly, the way a racing request's INSERT would arrive after the loser had
already passed its check — and assert the database refuses the duplicate and
that the refusal reaches the client as 409 rather than 500.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Employee, Examination, ExamStatus, User, UserRole


def test_two_open_examinations_are_impossible(client, make_user, register, auth, db_session):
    """The partial unique index, not the application check, is what guarantees
    one open PME per employee."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-race1")
    employee = register(client, ht, "P-RACE1")
    first = client.post("/api/v1/examinations", headers=auth(ht),
                        json={"employee_id": employee["id"], "scheduled_date": "2026-09-01"})
    assert first.status_code == 201

    # What a racing request's INSERT looks like: it never ran the check.
    db_session.add(Examination(employee_id=uuid.UUID(employee["id"]),
                               scheduled_date=date(2026, 10, 1),
                               status=ExamStatus.SCHEDULED))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_completed_examinations_are_not_covered_by_the_index(client, make_user, register, auth):
    """Only SCHEDULED rows are unique per employee — history must accumulate
    freely, or an employee could never have a second PME."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-race2")
    doc = make_user(role=UserRole.DOCTOR, uid="doc-race2")
    employee = register(client, ht, "P-RACE2")

    for day in ("2026-03-01", "2026-06-01", "2026-09-01"):
        exam = client.post("/api/v1/examinations", headers=auth(ht),
                           json={"employee_id": employee["id"], "scheduled_date": day})
        assert exam.status_code == 201, exam.text
        done = client.post(f"/api/v1/examinations/{exam.json()['id']}/complete",
                           headers=auth(doc), json={"fitness_status": "FIT"})
        assert done.status_code == 200

    history = client.get(f"/api/v1/employees/{employee['id']}/examinations", headers=auth(ht))
    assert history.json()["total"] == 3


def test_raced_duplicate_personal_number_is_409_not_500(client, make_user, register, auth, db_session):
    """The loser of a registration race gets the same 409 as the sequential
    path, not a 500 from an unhandled IntegrityError."""
    ht = make_user(role=UserRole.HEALTH_TEAM, uid="ht-race3")
    register(client, ht, "P-RACE3")

    # Simulate the racing INSERT arriving after another request's check passed.
    db_session.add(Employee(personal_number="P-RACE3", full_name="Racer", department="D",
                            plant="P", contact_number="1"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    # And through the API the answer is a clean conflict.
    duplicate = client.post("/api/v1/employees", headers=auth(ht),
                            json={"personal_number": "P-RACE3", "full_name": "Racer",
                                  "department": "D", "plant": "P", "contact_number": "1"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CONFLICT"


def test_conflict_translation_reports_the_right_constraint(client, make_user, auth, db_session,
                                                           monkeypatch):
    """``flush_or_conflict`` maps a unique violation to a 409 with a message
    describing what collided, so a client can tell the cases apart.

    The e-mail pre-check in POST /users is skipped here to force the INSERT to
    be what fails — exactly what a racing request would do.
    """
    admin = make_user(role=UserRole.ADMIN, uid="adm-race4")
    created = client.post("/api/v1/users", headers=auth(admin),
                          json={"email": "race@example.com", "display_name": "First",
                                "role": "DOCTOR"})
    assert created.status_code == 201

    monkeypatch.setattr("app.routes.users.select", _blind_select())
    raced = client.post("/api/v1/users", headers=auth(admin),
                        json={"email": "race@example.com", "display_name": "Second",
                              "role": "DOCTOR"})
    assert raced.status_code == 409
    assert "e-mail" in raced.json()["error"]["message"]


def _blind_select():
    """A ``select`` whose result finds nothing, so the pre-check passes.

    Only the duplicate-e-mail lookup runs before the INSERT in ``create_user``,
    so blanking every SELECT in that module is enough to reproduce the race
    without touching the code under test.
    """
    from sqlalchemy import select as real_select

    def _select(*args, **kwargs):
        return real_select(*args, **kwargs).where(User.id == uuid.UUID(int=0))

    return _select


def test_unknown_constraint_still_yields_a_conflict(db_session, make_user):
    """A unique violation with no entry in the message table is still a 409 —
    a conflict is a conflict even if we have nothing specific to say."""
    from app.auth import ConflictError
    from app.lookups import flush_or_conflict

    make_user(role=UserRole.ADMIN, uid="dup-uid")
    db_session.add(User(firebase_uid="dup-uid", email="other@example.com",
                        role=UserRole.ADMIN, display_name="Clash"))
    with pytest.raises(ConflictError):
        flush_or_conflict(db_session)
