"""ORM models. One module for the whole schema — grows with each backend
module and stays the single place to see every table.

Conventions (Database Design §1): UUID PKs, audit columns on business tables,
soft delete instead of hard delete, native Postgres types (tests run on real
Postgres, so no cross-dialect shims are needed).

Constraints and indexes are declared here in full and mirrored exactly by the
Alembic revisions. Tests build the schema with ``create_all``, so anything
declared only in a migration would never be exercised by a test — keep the two
in step.
"""

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.context import current_client_ip


def utcnow() -> datetime:
    """Current UTC time (single definition; freezable in tests)."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Project-wide declarative base."""


class AuditMixin:
    """created/updated stamps on every business table (AUD-3)."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=utcnow, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class SoftDeleteMixin:
    """Soft-delete columns; queries must filter ``deleted_at IS NULL``."""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class UserRole(str, enum.Enum):
    """Closed set of application roles (AUTH-4)."""

    EMPLOYEE = "EMPLOYEE"
    DOCTOR = "DOCTOR"
    HEALTH_TEAM = "HEALTH_TEAM"
    ADMIN = "ADMIN"


STAFF_ROLES = (UserRole.DOCTOR, UserRole.HEALTH_TEAM, UserRole.ADMIN)
"""Roles that see the whole population; EMPLOYEE only ever sees itself."""


class User(Base, AuditMixin, SoftDeleteMixin):
    """Login account keyed to Firebase by firebase_uid; role lives here."""

    __tablename__ = "users"
    __table_args__ = (
        # Named explicitly rather than left to SQLAlchemy's default, so the
        # name matches the migration — see tests/test_schema_parity.py. The
        # constraint's implicit index is what lookups by uid use.
        UniqueConstraint("firebase_uid", name="uq_users_firebase_uid"),
        # E-mail is unique among live rows only, so a soft-deleted account
        # never blocks re-creating the same person.
        Index("uq_users_email_live", "email", unique=True,
              postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    firebase_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """Append-only audit trail (AUD-1/2); no FKs so rows outlive referents.

    ``summary`` holds technical metadata / changed field NAMES only — never
    clinical values (SEC-8).
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_actor_time", "actor_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    actor_role: Mapped[UserRole | None] = mapped_column(
        Enum(UserRole, name="user_role", create_type=False), nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 nullable=False, index=True)


class ExamStatus(str, enum.Enum):
    """Simplified exam lifecycle for the MVP slice (full DOC-8 machine later)."""

    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class FitnessStatus(str, enum.Enum):
    """Fitness outcome of a completed examination."""

    FIT = "FIT"
    TEMPORARILY_UNFIT = "TEMPORARILY_UNFIT"
    UNFIT = "UNFIT"


class Employee(Base, AuditMixin, SoftDeleteMixin):
    """An employee undergoing PMEs (EMP-1, simplified: dept/plant are text).

    ``user_id`` links to a login account when the employee has one, so they
    can view their own status (EMP-2/EMP-5).
    """

    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_employees_user_id"),
        # Personal number unique among live rows only — same reasoning as
        # uq_users_email_live, and it lets a soft-deleted employee's number
        # be reused.
        Index("uq_employees_personal_number_live", "personal_number", unique=True,
              postgresql_where=text("deleted_at IS NULL")),
        # Trigram indexes back the ILIKE '%q%' search in GET /employees
        # (SRCH-1); without them that search is a sequential scan.
        Index("ix_employees_full_name_trgm", "full_name",
              postgresql_using="gin", postgresql_ops={"full_name": "gin_trgm_ops"}),
        Index("ix_employees_personal_number_trgm", "personal_number",
              postgresql_using="gin", postgresql_ops={"personal_number": "gin_trgm_ops"}),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    personal_number: Mapped[str] = mapped_column(String(30), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    department: Mapped[str] = mapped_column(String(120), nullable=False)
    plant: Mapped[str] = mapped_column(String(120), nullable=False)
    contact_number: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", name="fk_employees_user_id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Examination(Base, AuditMixin):
    """One PME (simplified: flat vital-sign columns instead of the full
    parameter/EAV model — see DATABASE_DESIGN.md for the target shape).
    """

    __tablename__ = "examinations"
    __table_args__ = (
        # "One open PME per employee" as a database rule, not a hopeful
        # check-then-insert: two concurrent schedule requests would both pass an
        # application-level check and leave the employee with two open exams and
        # no single current status. Only SCHEDULED rows are covered, so an
        # employee can accumulate any number of completed/cancelled ones.
        Index("uq_examinations_one_open_per_employee", "employee_id", unique=True,
              postgresql_where=text("status = 'SCHEDULED'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("employees.id"), nullable=False, index=True)
    doctor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True)
    status: Mapped[ExamStatus] = mapped_column(Enum(ExamStatus, name="exam_status"),
                                               nullable=False, default=ExamStatus.SCHEDULED)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fitness_status: Mapped[FitnessStatus | None] = mapped_column(
        Enum(FitnessStatus, name="fitness_status"), nullable=True)
    bp_systolic: Mapped[int | None] = mapped_column(nullable=True)
    bp_diastolic: Mapped[int | None] = mapped_column(nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


def record_audit(db, actor, action: str, entity_type: str,
                 entity_id: uuid.UUID | None = None, ip_address: str | None = None,
                 summary: dict | None = None) -> None:
    """Append one audit row inside the caller's transaction (AUD-1).

    Args:
        db: Active session — audit commits/rolls back atomically with the change.
        actor: CurrentUser or None for system jobs.
        action: CREATE / UPDATE / SOFT_DELETE / LOGIN / EXPORT / ...
        entity_type: Aggregate name, e.g. "employee".
        entity_id: Affected row id, when applicable.
        ip_address: Client IP; defaults to the in-flight request's, so callers
            inside a request never have to pass it.
        summary: Field NAMES / technical metadata only — never values.

    """
    db.add(AuditLog(actor_user_id=getattr(actor, "id", None),
                    actor_role=getattr(actor, "role", None),
                    action=action, entity_type=entity_type, entity_id=entity_id,
                    ip_address=ip_address or current_client_ip(), summary=summary))
