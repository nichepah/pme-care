"""Seed one demo login per role for local development.

Run once after ``alembic upgrade head``:

    python -m scripts.seed_dev

Prints the bearer tokens and ids the demo frontend asks for. In AUTH_FAKE_MODE
(the local default) a bearer token IS the firebase_uid, so these printed strings
are exactly what goes in the token fields.

Idempotent: run it as often as you like, it only inserts what is missing.
"""

from datetime import date, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Employee, Examination, ExamStatus, FitnessStatus, User, UserRole

DEMO_USERS = [
    ("dev-admin", UserRole.ADMIN, "Dev Admin"),
    ("dev-healthteam", UserRole.HEALTH_TEAM, "Dev Health Team"),
    ("dev-doctor", UserRole.DOCTOR, "Dev Doctor"),
    ("dev-employee", UserRole.EMPLOYEE, "Dev Employee"),
]

DEMO_EMPLOYEES = [
    # (personal_number, name, department, plant, linked to the dev-employee login?)
    ("DEV-001", "Dev Employee", "Demo Department", "Demo Plant", True),
    ("DEV-002", "Unlinked Employee", "Foundry", "Plant 2", False),
    ("DEV-003", "Overdue Person", "Foundry", "Plant 2", False),
    ("DEV-004", "Due Soon Person", "Rolling Mill", "Plant 1", False),
]

# History that makes GET /employees/due show something on a fresh database:
# one badly overdue, one falling due inside the default window, and two who
# have never been examined at all.
#  (personal_number, days since the examination, days until the next is due)
DEMO_HISTORY = [
    ("DEV-003", 400, -35),   # examined over a year ago, 35 days overdue
    ("DEV-004", 350, 15),    # due in a fortnight — the booking window
]


def _ensure_users(db) -> dict[str, User]:
    """Insert any missing demo account; return them all by uid."""
    accounts = {}
    for uid, role, name in DEMO_USERS:
        user = db.execute(select(User).where(User.firebase_uid == uid)).scalar_one_or_none()
        if user is None:
            user = User(firebase_uid=uid, email=f"{uid}@example.com", role=role,
                        display_name=name)
            db.add(user)
            db.flush()
        accounts[uid] = user
    return accounts


def _ensure_employees(db, employee_login: User) -> dict[str, Employee]:
    """Insert any missing demo employee; return them all by personal number."""
    employees = {}
    for number, name, department, plant, linked in DEMO_EMPLOYEES:
        employee = db.execute(select(Employee).where(
            Employee.personal_number == number,
            Employee.deleted_at.is_(None))).scalar_one_or_none()
        if employee is None:
            employee = Employee(personal_number=number, full_name=name, department=department,
                                plant=plant, contact_number="0000000000",
                                user_id=employee_login.id if linked else None)
            db.add(employee)
            db.flush()
        employees[number] = employee
    return employees


def _ensure_history(db, employees: dict[str, Employee], doctor: User) -> None:
    """Give a couple of employees a completed examination in the past.

    Dates are relative to today so the compliance list is meaningful whenever
    the script is run, rather than only in the week it was written.
    """
    today = date.today()
    for number, examined_days_ago, due_in_days in DEMO_HISTORY:
        employee = employees[number]
        existing = db.execute(select(Examination).where(
            Examination.employee_id == employee.id)).scalars().first()
        if existing is not None:
            continue
        exam_date = today - timedelta(days=examined_days_ago)
        db.add(Examination(employee_id=employee.id, doctor_user_id=doctor.id,
                           status=ExamStatus.COMPLETED, scheduled_date=exam_date,
                           exam_date=exam_date, next_due_date=today + timedelta(days=due_in_days),
                           fitness_status=FitnessStatus.FIT,
                           bp_systolic=120, bp_diastolic=80,
                           remarks="Seeded history for the compliance demo."))


def run() -> None:
    """Seed the demo accounts and employees, then print what to paste where."""
    with SessionLocal() as db:
        accounts = _ensure_users(db)
        employees = _ensure_employees(db, accounts["dev-employee"])
        _ensure_history(db, employees, accounts["dev-doctor"])
        db.commit()

        print("Bearer tokens (AUTH_FAKE_MODE only):")
        for uid, role, _name in DEMO_USERS:
            print(f"  {role.value:<12} {uid}")

        print("\nIds the demo UI asks for:")
        print(f"  employee id (has a login, use in the Employee panel)  {employees['DEV-001'].id}")
        print(f"  employee id (no login — try 'Give login' on it)       {employees['DEV-002'].id}")
        print(f"  doctor user id (for the optional 'assign doctor')     {accounts['dev-doctor'].id}")
        print("\nThe compliance list (GET /employees/due) starts with:")
        print("  DEV-003  examined 400 days ago, 35 days overdue")
        print("  DEV-004  due in 15 days")
        print("  DEV-001, DEV-002  never examined")


if __name__ == "__main__":
    run()
