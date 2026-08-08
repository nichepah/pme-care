"""Employee endpoints (EMP-1, EMP-2, EMP-5, HT-1, SRCH-1).

Health Team/Admin register, amend and retire employees. Doctors/Health
Team/Admin can look up anyone. An EMPLOYEE-role user can only ever see their
own record — enforced by comparing against the employee row linked to their
account, never by trusting an id in the URL.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.auth import (
    BusinessRuleError,
    ConflictError,
    CurrentUser,
    NotFoundError,
    get_current_user,
    require_roles,
)
from app.config import settings
from app.db import get_db
from app.lookups import flush_or_conflict, parse_uuid
from app.models import STAFF_ROLES, Employee, Examination, ExamStatus, User, UserRole, record_audit, utcnow
from app.paging import PageParams, page_params, paginate
from app.provisioning import get_provisioner
from app.routes.examinations import exam_to_out
from app.schemas import (
    EmployeeCreate,
    EmployeeLoginOut,
    EmployeeOut,
    EmployeeStatus,
    EmployeeUpdate,
    ExaminationOut,
    ExaminationSummary,
    Page,
)

router = APIRouter(prefix="/employees", tags=["employees"])

NOT_FOUND = "Employee not found."


def _to_out(e: Employee) -> EmployeeOut:
    """Map an Employee row to its API representation."""
    return EmployeeOut(id=str(e.id), personal_number=e.personal_number, full_name=e.full_name,
                       department=e.department, plant=e.plant, contact_number=e.contact_number,
                       email=e.email, is_active=e.is_active)


def _load(db: Session, employee_id: str, user: CurrentUser) -> Employee:
    """Fetch a live employee the caller is allowed to see, or raise 404.

    An EMPLOYEE-role caller asking for somebody else gets the same 404 as an
    id that does not exist, so the endpoint never confirms that another
    employee record is there (anti-enumeration).
    """
    employee = db.execute(select(Employee).where(Employee.id == parse_uuid(employee_id, NOT_FOUND),
                                                 Employee.deleted_at.is_(None))).scalar_one_or_none()
    if employee is None:
        raise NotFoundError(NOT_FOUND)
    if user.role == UserRole.EMPLOYEE and employee.user_id != user.id:
        raise NotFoundError(NOT_FOUND)
    return employee


def _latest_exam_summary(db: Session, employee_id: uuid.UUID) -> ExaminationSummary | None:
    """Fetch the most recently created examination for an employee, if any."""
    exam = db.execute(
        select(Examination).where(Examination.employee_id == employee_id)
        .order_by(Examination.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if exam is None:
        return None
    return ExaminationSummary(id=str(exam.id), status=exam.status, scheduled_date=exam.scheduled_date,
                              exam_date=exam.exam_date, fitness_status=exam.fitness_status,
                              remarks=exam.remarks)


@router.post("", response_model=EmployeeOut, status_code=201)
def create_employee(body: EmployeeCreate,
                    user: CurrentUser = Depends(require_roles(UserRole.HEALTH_TEAM, UserRole.ADMIN)),
                    db: Session = Depends(get_db)) -> EmployeeOut:
    """Register a new employee (HT-1). 409 on duplicate personal number.

    The pre-check gives the better message in the ordinary case; the partial
    unique index behind ``flush_or_conflict`` is what actually guarantees
    uniqueness when two registrations race.
    """
    exists = db.execute(select(Employee).where(Employee.personal_number == body.personal_number,
                                               Employee.deleted_at.is_(None))).scalar_one_or_none()
    if exists is not None:
        raise ConflictError(f"An employee with personal number {body.personal_number} already exists.")
    employee = Employee(personal_number=body.personal_number, full_name=body.full_name,
                        department=body.department, plant=body.plant,
                        contact_number=body.contact_number, email=body.email, created_by=user.id)
    db.add(employee)
    flush_or_conflict(db)
    record_audit(db, user, "CREATE", "employee", employee.id)
    return _to_out(employee)


def _search_filter(stmt: Select, q: str | None) -> Select:
    """Apply the free-text filter to an employee query (SRCH-1).

    Matches name or personal number, case-insensitively, anywhere in the value
    — backed by the trigram indexes in migration 0003.
    """
    if not q or not q.strip():
        return stmt
    pattern = f"%{q.strip()}%"
    return stmt.where(or_(Employee.full_name.ilike(pattern),
                          Employee.personal_number.ilike(pattern)))


@router.get("", response_model=Page[EmployeeOut])
def list_employees(q: str | None = Query(None, max_length=100,
                                         description="Match name or personal number"),
                   department: str | None = Query(None, max_length=120),
                   plant: str | None = Query(None, max_length=120),
                   is_active: bool | None = Query(None),
                   params: PageParams = Depends(page_params),
                   user: CurrentUser = Depends(require_roles(*STAFF_ROLES)),
                   db: Session = Depends(get_db)) -> Page[EmployeeOut]:
    """Search/list employees by name (SRCH-1).

    Active employees only unless ``is_active=false`` asks for retired ones.
    There is deliberately no "both": mixing current and retired staff in one
    list is how someone gets scheduled for a PME they no longer need, and every
    caller so far wants one or the other.
    """
    stmt = select(Employee).where(Employee.deleted_at.is_(None))
    stmt = _search_filter(stmt, q)
    if department:
        stmt = stmt.where(Employee.department == department)
    if plant:
        stmt = stmt.where(Employee.plant == plant)
    stmt = stmt.where(Employee.is_active.is_(True if is_active is None else is_active))
    return paginate(db, stmt.order_by(Employee.full_name, Employee.id), params, _to_out)


@router.get("/{employee_id}", response_model=EmployeeStatus)
def get_employee(employee_id: str, user: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> EmployeeStatus:
    """Employee detail + latest examination (EMP-2)."""
    employee = _load(db, employee_id, user)
    return EmployeeStatus(**_to_out(employee).model_dump(),
                          latest_examination=_latest_exam_summary(db, employee.id))


@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: str, body: EmployeeUpdate,
                    user: CurrentUser = Depends(require_roles(UserRole.HEALTH_TEAM, UserRole.ADMIN)),
                    db: Session = Depends(get_db)) -> EmployeeOut:
    """Amend an employee's details (HT-1); only the fields sent are changed.

    Setting ``is_active=false`` retires someone without deleting them: their
    examination history stays intact and they drop out of the default list.
    """
    employee = _load(db, employee_id, user)
    changed = body.model_dump(exclude_unset=True)
    if not changed:
        return _to_out(employee)
    for field, value in changed.items():
        setattr(employee, field, value)
    employee.updated_by = user.id
    db.flush()
    # Field NAMES only — an audit row must never carry personal data (SEC-8).
    record_audit(db, user, "UPDATE", "employee", employee.id,
                 summary={"fields_changed": sorted(changed)})
    return _to_out(employee)


@router.delete("/{employee_id}", status_code=204)
def delete_employee(employee_id: str,
                    user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
                    db: Session = Depends(get_db)) -> None:
    """Soft-delete an employee (never a hard delete — the medical history and
    its audit trail have to survive).

    Refuses while a PME is still open, so an exam can never be orphaned by a
    deletion; cancel it first. Any linked login is deactivated at the same
    time, otherwise the person could still authenticate afterwards.
    """
    employee = _load(db, employee_id, user)
    open_exam = db.execute(select(Examination).where(
        Examination.employee_id == employee.id, Examination.status == ExamStatus.SCHEDULED
    ).limit(1)).scalar_one_or_none()
    if open_exam is not None:
        raise ConflictError(
            f"Employee has an open examination ({open_exam.id}); cancel it before deleting.")

    employee.deleted_at, employee.deleted_by = utcnow(), user.id
    employee.is_active = False
    record_audit(db, user, "SOFT_DELETE", "employee", employee.id)

    if employee.user_id is not None:
        account = db.get(User, employee.user_id)
        if account is not None and account.is_active:
            account.is_active = False
            account.updated_by = user.id
            record_audit(db, user, "UPDATE", "user", account.id,
                         summary={"fields_changed": ["is_active"], "reason": "employee_deleted"})
    db.flush()


@router.get("/{employee_id}/examinations", response_model=Page[ExaminationOut])
def list_employee_examinations(employee_id: str,
                               status: ExamStatus | None = Query(None),
                               params: PageParams = Depends(page_params),
                               user: CurrentUser = Depends(get_current_user),
                               db: Session = Depends(get_db)) -> Page[ExaminationOut]:
    """An employee's examination history, most recent first (EMP-2).

    Same scope rule as the detail endpoint: staff see anyone, an employee sees
    only their own.
    """
    employee = _load(db, employee_id, user)
    stmt = select(Examination).where(Examination.employee_id == employee.id)
    if status is not None:
        stmt = stmt.where(Examination.status == status)
    return paginate(db, stmt.order_by(Examination.scheduled_date.desc(), Examination.id),
                    params, exam_to_out)


@router.post("/{employee_id}/login", response_model=EmployeeLoginOut, status_code=201)
def create_employee_login(employee_id: str,
                          user: CurrentUser = Depends(require_roles(UserRole.HEALTH_TEAM, UserRole.ADMIN)),
                          db: Session = Depends(get_db)) -> EmployeeLoginOut:
    """Create a login for an employee and link it, closing the gap between
    "registered" and "can view their own status" (EMP-5).

    Needs the employee's e-mail address in production: that is where the sign-in
    link goes, and without one there is no way for them to prove who they are.
    In AUTH_FAKE_MODE a placeholder address is enough, since the returned token
    authenticates directly.
    """
    employee = _load(db, employee_id, user)
    if employee.user_id is not None:
        raise ConflictError("This employee already has a login.")
    if not employee.email and not settings.AUTH_FAKE_MODE:
        raise BusinessRuleError(
            "This employee has no e-mail address, so a sign-in link cannot be sent. "
            "Add one first.", details=[{"field": "email", "issue": "required for login"}])

    identity = get_provisioner("emp").create(
        db, email=employee.email or f"emp-{employee.personal_number}@example.local",
        display_name=employee.full_name)
    account = User(firebase_uid=identity.firebase_uid,
                   email=employee.email or f"{identity.firebase_uid}@example.local",
                   role=UserRole.EMPLOYEE, display_name=employee.full_name, created_by=user.id)
    db.add(account)
    # Flush before reading account.id — the primary key is assigned on INSERT,
    # so linking first would store NULL.
    flush_or_conflict(db)
    employee.user_id = account.id
    employee.updated_by = user.id
    # Second flush: a raced call to this endpoint collides on
    # uq_employees_user_id rather than quietly stealing the link.
    flush_or_conflict(db)
    record_audit(db, user, "CREATE", "user", account.id,
                 summary={"created_for_employee": str(employee.id)})
    record_audit(db, user, "UPDATE", "employee", employee.id,
                 summary={"fields_changed": ["user_id"]})
    return EmployeeLoginOut(employee_id=str(employee.id), user_id=str(account.id),
                            dev_bearer_token=identity.dev_bearer_token,
                            sign_in_link=identity.sign_in_link)
