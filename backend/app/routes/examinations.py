"""Examination endpoints (HT-2, HT-4, DOC-1, DOC-3, DOC-6).

Lifecycle here is deliberately just SCHEDULED -> COMPLETED | CANCELLED, not
the full state machine in API_DESIGN.md — enough to run the real Health
Team -> Doctor -> Employee flow end to end. Both exits are terminal: a
completed or cancelled PME is never reopened, a new one is scheduled instead,
so the record of what was decided when stays immutable.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import BusinessRuleError, ConflictError, CurrentUser, NotFoundError, get_current_user, require_roles
from app.db import get_db
from app.lookups import flush_or_conflict, parse_uuid
from app.models import STAFF_ROLES, Employee, Examination, ExamStatus, FitnessStatus, User, UserRole, record_audit
from app.paging import PageParams, page_params, paginate
from app.schemas import ExaminationCancel, ExaminationComplete, ExaminationCreate, ExaminationOut, Page

router = APIRouter(prefix="/examinations", tags=["examinations"])

NOT_FOUND = "Examination not found."


def exam_to_out(x: Examination) -> ExaminationOut:
    """Map an Examination row to its API representation.

    Shared with the employee router's history endpoint, so one examination has
    exactly one wire shape wherever it is returned.
    """
    return ExaminationOut(
        id=str(x.id), employee_id=str(x.employee_id),
        doctor_user_id=str(x.doctor_user_id) if x.doctor_user_id else None,
        status=x.status, scheduled_date=x.scheduled_date, exam_date=x.exam_date,
        fitness_status=x.fitness_status, bp_systolic=x.bp_systolic, bp_diastolic=x.bp_diastolic,
        height_cm=float(x.height_cm) if x.height_cm is not None else None,
        weight_kg=float(x.weight_kg) if x.weight_kg is not None else None,
        remarks=x.remarks, cancel_reason=x.cancel_reason,
    )


def _load(db: Session, examination_id: str) -> Examination:
    """Fetch an examination or raise 404 (no scope check — callers add theirs)."""
    exam = db.execute(select(Examination).where(
        Examination.id == parse_uuid(examination_id, NOT_FOUND))).scalar_one_or_none()
    if exam is None:
        raise NotFoundError(NOT_FOUND)
    return exam


def _require_open(exam: Examination, action: str) -> None:
    """Reject an action on an examination that has already left SCHEDULED."""
    if exam.status != ExamStatus.SCHEDULED:
        raise BusinessRuleError(
            f"Examination is already {exam.status.value.lower()}; cannot {action} it.")


def _resolve_doctor(db: Session, doctor_user_id: str | None) -> User | None:
    """Validate an optional assigned-doctor id.

    A bad id here is the client's mistake, not a missing examination, so it is
    a 422 naming the field rather than a 404 — and catching it up front stops
    a foreign-key violation surfacing as a 500.
    """
    if doctor_user_id is None:
        return None
    try:
        doctor_uuid = uuid.UUID(doctor_user_id)
    except ValueError as exc:
        raise BusinessRuleError("doctor_user_id is not a valid id.",
                                details=[{"field": "doctor_user_id", "issue": "malformed"}]) from exc
    doctor = db.execute(select(User).where(User.id == doctor_uuid,
                                           User.deleted_at.is_(None))).scalar_one_or_none()
    if doctor is None or not doctor.is_active or doctor.role != UserRole.DOCTOR:
        raise BusinessRuleError(
            "doctor_user_id must be an active user with the DOCTOR role.",
            details=[{"field": "doctor_user_id", "issue": "not an active doctor"}])
    return doctor


@router.post("", response_model=ExaminationOut, status_code=201)
def schedule_examination(body: ExaminationCreate,
                         user: CurrentUser = Depends(require_roles(UserRole.HEALTH_TEAM, UserRole.ADMIN)),
                         db: Session = Depends(get_db)) -> ExaminationOut:
    """Schedule a PME for an employee (HT-2).

    409 if one is already open: two concurrently open PMEs for one person would
    make "their current status" ambiguous.
    """
    employee = db.execute(select(Employee).where(
        Employee.id == parse_uuid(body.employee_id, "Employee not found."),
        Employee.deleted_at.is_(None))).scalar_one_or_none()
    if employee is None:
        raise NotFoundError("Employee not found.")
    if not employee.is_active:
        raise BusinessRuleError("Cannot schedule an examination for a retired employee.")

    open_exam = db.execute(select(Examination).where(
        Examination.employee_id == employee.id, Examination.status == ExamStatus.SCHEDULED
    ).limit(1)).scalar_one_or_none()
    if open_exam is not None:
        raise ConflictError(f"Employee already has an open examination ({open_exam.id}).")

    doctor = _resolve_doctor(db, body.doctor_user_id)
    exam = Examination(employee_id=employee.id, doctor_user_id=doctor.id if doctor else None,
                       scheduled_date=body.scheduled_date, created_by=user.id)
    db.add(exam)
    # The check above is the readable path; the partial unique index reached
    # through here is what holds when two schedule requests race.
    flush_or_conflict(db)
    record_audit(db, user, "CREATE", "examination", exam.id)
    return exam_to_out(exam)


@router.get("", response_model=Page[ExaminationOut])
def list_examinations(status: ExamStatus | None = Query(None),
                      employee_id: str | None = Query(None),
                      doctor_user_id: str | None = Query(None),
                      scheduled_from: date | None = Query(None, description="Inclusive lower bound"),
                      scheduled_to: date | None = Query(None, description="Inclusive upper bound"),
                      params: PageParams = Depends(page_params),
                      user: CurrentUser = Depends(require_roles(*STAFF_ROLES)),
                      db: Session = Depends(get_db)) -> Page[ExaminationOut]:
    """The worklist: examinations by status, employee, doctor and date window
    (HT-4 / DOC-1), soonest first.
    """
    stmt = select(Examination)
    if status is not None:
        stmt = stmt.where(Examination.status == status)
    if employee_id:
        stmt = stmt.where(Examination.employee_id == parse_uuid(employee_id, "Employee not found."))
    if doctor_user_id:
        stmt = stmt.where(Examination.doctor_user_id == parse_uuid(doctor_user_id, "Doctor not found."))
    if scheduled_from:
        stmt = stmt.where(Examination.scheduled_date >= scheduled_from)
    if scheduled_to:
        stmt = stmt.where(Examination.scheduled_date <= scheduled_to)
    return paginate(db, stmt.order_by(Examination.scheduled_date, Examination.id),
                    params, exam_to_out)


@router.get("/{examination_id}", response_model=ExaminationOut)
def get_examination(examination_id: str, user: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> ExaminationOut:
    """One examination in full. An EMPLOYEE-role caller may only read their
    own; anything else is a 404, as everywhere else (anti-enumeration).
    """
    exam = _load(db, examination_id)
    if user.role == UserRole.EMPLOYEE:
        owner = db.execute(select(Employee).where(Employee.id == exam.employee_id,
                                                  Employee.deleted_at.is_(None))).scalar_one_or_none()
        if owner is None or owner.user_id != user.id:
            raise NotFoundError(NOT_FOUND)
    return exam_to_out(exam)


@router.post("/{examination_id}/complete", response_model=ExaminationOut)
def complete_examination(examination_id: str, body: ExaminationComplete,
                         user: CurrentUser = Depends(require_roles(UserRole.DOCTOR)),
                         db: Session = Depends(get_db)) -> ExaminationOut:
    """Record the outcome of an examination (DOC-3/DOC-6).

    Business rule (simplified DOC-6): anything short of FIT must carry remarks
    explaining why, since those outcomes have real consequences for the
    employee and someone will have to justify them later.
    """
    exam = _load(db, examination_id)
    _require_open(exam, "complete")
    if body.fitness_status != FitnessStatus.FIT and not (body.remarks and body.remarks.strip()):
        raise BusinessRuleError(
            f"Remarks are required when the fitness decision is {body.fitness_status.value}.",
            details=[{"field": "remarks", "issue": "required"}])

    exam.status = ExamStatus.COMPLETED
    exam.exam_date = date.today()
    exam.doctor_user_id = user.id
    exam.fitness_status = body.fitness_status
    exam.bp_systolic = body.bp_systolic
    exam.bp_diastolic = body.bp_diastolic
    exam.height_cm = body.height_cm
    exam.weight_kg = body.weight_kg
    exam.remarks = body.remarks
    exam.updated_by = user.id
    db.flush()
    # Field NAMES only in the audit summary — never the clinical values (SEC-8).
    record_audit(db, user, "UPDATE", "examination", exam.id,
                 summary={"fields_changed": ["status", "fitness_status", "bp_systolic",
                                             "bp_diastolic", "height_cm", "weight_kg", "remarks"]})
    return exam_to_out(exam)


@router.post("/{examination_id}/cancel", response_model=ExaminationOut)
def cancel_examination(examination_id: str, body: ExaminationCancel,
                       user: CurrentUser = Depends(require_roles(UserRole.HEALTH_TEAM, UserRole.ADMIN)),
                       db: Session = Depends(get_db)) -> ExaminationOut:
    """Cancel a scheduled PME with a mandatory reason (HT-2).

    Cancelling frees the employee to be scheduled again — the open-examination
    conflict only counts SCHEDULED rows.
    """
    exam = _load(db, examination_id)
    _require_open(exam, "cancel")
    exam.status = ExamStatus.CANCELLED
    exam.cancel_reason = body.reason.strip()
    exam.updated_by = user.id
    db.flush()
    record_audit(db, user, "UPDATE", "examination", exam.id,
                 summary={"fields_changed": ["status", "cancel_reason"]})
    return exam_to_out(exam)
