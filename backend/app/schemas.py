"""Request/response schemas for the whole API surface.

Grouped by aggregate: shared envelopes, identity, users (admin), employees,
examinations, audit log. Request models validate; response models are built
explicitly in the routes so no ORM row is ever serialized by accident.
"""

import re
from datetime import date, datetime
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, Field, field_validator

from app.models import ExamStatus, FitnessStatus, UserRole

T = TypeVar("T")

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _normalize_email(value: str) -> str:
    """Trim and sanity-check an address.

    Deliberately a shape check, not RFC 5322: full validation needs the
    ``email-validator`` package, and the only thing this API does with an
    address is store it, so a new dependency would not buy anything. Delivery
    is what actually proves an address, and nothing here sends mail yet.
    """
    email = value.strip()
    if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
        raise ValueError("Not a valid e-mail address.")
    return email


Email = Annotated[str, AfterValidator(_normalize_email)]


class Page(BaseModel, Generic[T]):
    """Standard paginated list response (API spec §1.4)."""

    items: list[T]
    total: int
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)


class MeResponse(BaseModel):
    """Body of GET /me."""

    id: str
    email: str
    display_name: str
    role: UserRole


# --- Users (ADMIN) ----------------------------------------------------------

class UserCreate(BaseModel):
    """Body of POST /users — Admin provisions a staff account."""

    email: Email
    display_name: str = Field(min_length=1, max_length=150)
    role: UserRole

    @field_validator("role")
    @classmethod
    def staff_roles_only(cls, value: UserRole) -> UserRole:
        """EMPLOYEE accounts come from POST /employees/{id}/login instead, so
        that every employee login is linked to an employee record."""
        if value == UserRole.EMPLOYEE:
            raise ValueError("Use POST /employees/{id}/login to create an EMPLOYEE account.")
        return value


class UserUpdate(BaseModel):
    """Body of PATCH /users/{id}. Omitted fields are left untouched."""

    display_name: str | None = Field(default=None, min_length=1, max_length=150)
    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def staff_roles_only(cls, value: UserRole | None) -> UserRole | None:
        """An account cannot be demoted into EMPLOYEE — that role implies a
        linked employee record this endpoint knows nothing about."""
        if value == UserRole.EMPLOYEE:
            raise ValueError("An account cannot be changed to the EMPLOYEE role.")
        return value


class UserOut(BaseModel):
    """One account as returned by the admin user endpoints."""

    id: str
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None


class UserCreatedOut(UserOut):
    """POST /users response: the account plus how its owner gets in.

    Exactly one of the two is populated. ``dev_bearer_token`` appears only in
    AUTH_FAKE_MODE, where a uid authenticates directly; in production the user
    follows ``sign_in_link`` and chooses their own credential, so this service
    never holds one. ``sign_in_link`` may also be None if the provider created
    the account but could not generate a link — the account is still valid and
    the normal e-mail sign-in flow works.
    """

    dev_bearer_token: str | None = None
    sign_in_link: str | None = None


# --- Employees --------------------------------------------------------------

class EmployeeCreate(BaseModel):
    """Body of POST /employees (Health Team registers a new employee)."""

    personal_number: str = Field(min_length=1, max_length=30)
    full_name: str = Field(min_length=1, max_length=150)
    department: str = Field(min_length=1, max_length=120)
    plant: str = Field(min_length=1, max_length=120)
    contact_number: str = Field(min_length=1, max_length=20)
    email: Email | None = None


class EmployeeUpdate(BaseModel):
    """Body of PATCH /employees/{id}. Omitted fields are left untouched.

    ``personal_number`` is deliberately absent: it is the employer's identifier
    for the person and the key other records are reconciled against, so it is
    set once at registration.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    department: str | None = Field(default=None, min_length=1, max_length=120)
    plant: str | None = Field(default=None, min_length=1, max_length=120)
    contact_number: str | None = Field(default=None, min_length=1, max_length=20)
    email: Email | None = None
    is_active: bool | None = None


class EmployeeOut(BaseModel):
    """One employee, as returned by list/detail endpoints."""

    id: str
    personal_number: str
    full_name: str
    department: str
    plant: str
    contact_number: str
    email: str | None
    is_active: bool


class ExaminationSummary(BaseModel):
    """Short examination summary embedded in an employee's status view."""

    id: str
    status: ExamStatus
    scheduled_date: date
    exam_date: date | None
    fitness_status: FitnessStatus | None
    remarks: str | None


class EmployeeStatus(EmployeeOut):
    """Employee detail plus their latest examination (EMP-2)."""

    latest_examination: ExaminationSummary | None


class EmployeeLoginOut(BaseModel):
    """Response of POST /employees/{id}/login.

    Same two-mode contract as ``UserCreatedOut``: a working token in fake mode,
    a sign-in link in production.
    """

    employee_id: str
    user_id: str
    dev_bearer_token: str | None = None
    sign_in_link: str | None = None


# --- Examinations -----------------------------------------------------------

class ExaminationCreate(BaseModel):
    """Body of POST /examinations (Health Team schedules a PME)."""

    employee_id: str
    scheduled_date: date
    doctor_user_id: str | None = None


class ExaminationOut(BaseModel):
    """One examination as returned by list/detail endpoints."""

    id: str
    employee_id: str
    doctor_user_id: str | None
    status: ExamStatus
    scheduled_date: date
    exam_date: date | None
    fitness_status: FitnessStatus | None
    bp_systolic: int | None
    bp_diastolic: int | None
    height_cm: float | None
    weight_kg: float | None
    remarks: str | None
    cancel_reason: str | None


class ExaminationComplete(BaseModel):
    """Body of POST /examinations/{id}/complete (Doctor records the outcome)."""

    fitness_status: FitnessStatus
    bp_systolic: int | None = Field(default=None, ge=40, le=300)
    bp_diastolic: int | None = Field(default=None, ge=20, le=200)
    height_cm: float | None = Field(default=None, gt=0, le=999.9)
    weight_kg: float | None = Field(default=None, gt=0, le=999.9)
    remarks: str | None = None


class ExaminationCancel(BaseModel):
    """Body of POST /examinations/{id}/cancel. A reason is mandatory: a
    cancelled PME is a compliance gap that someone has to be able to explain.
    """

    reason: str = Field(min_length=1, max_length=2000)


# --- Audit log (ADMIN) ------------------------------------------------------

class AuditLogOut(BaseModel):
    """One audit row. ``summary`` carries field names / technical metadata
    only — never clinical values (SEC-8).
    """

    id: int
    actor_user_id: str | None
    actor_role: UserRole | None
    action: str
    entity_type: str
    entity_id: str | None
    ip_address: str | None
    summary: dict | None
    created_at: datetime
