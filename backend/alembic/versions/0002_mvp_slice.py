"""0002 — MVP slice: employees, examinations, exam_status/fitness_status enums.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

EXAM_STATUS = ("SCHEDULED", "COMPLETED", "CANCELLED")
FITNESS_STATUS = ("FIT", "TEMPORARILY_UNFIT", "UNFIT")


def upgrade() -> None:
    """Create employees and examinations, plus their enum types."""
    postgresql.ENUM(*EXAM_STATUS, name="exam_status").create(op.get_bind(), checkfirst=True)
    postgresql.ENUM(*FITNESS_STATUS, name="fitness_status").create(op.get_bind(), checkfirst=True)

    op.create_table(
        "employees",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("personal_number", sa.String(30), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("department", sa.String(120), nullable=False),
        sa.Column("plant", sa.String(120), nullable=False),
        sa.Column("contact_number", sa.String(20), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
    )
    op.create_index("uq_employees_personal_number_live", "employees", ["personal_number"],
                    unique=True, postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_unique_constraint("uq_employees_user_id", "employees", ["user_id"])

    op.create_table(
        "examinations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("employee_id", sa.Uuid(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("doctor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", postgresql.ENUM(*EXAM_STATUS, name="exam_status", create_type=False),
                  nullable=False, server_default="SCHEDULED"),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=True),
        sa.Column("fitness_status", postgresql.ENUM(*FITNESS_STATUS, name="fitness_status",
                                                     create_type=False), nullable=True),
        sa.Column("bp_systolic", sa.Integer(), nullable=True),
        sa.Column("bp_diastolic", sa.Integer(), nullable=True),
        sa.Column("height_cm", sa.Numeric(5, 1), nullable=True),
        sa.Column("weight_kg", sa.Numeric(5, 1), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_examinations_employee_id", "examinations", ["employee_id"])


def downgrade() -> None:
    """Drop everything created by this revision."""
    op.drop_table("examinations")
    op.drop_table("employees")
    postgresql.ENUM(name="fitness_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="exam_status").drop(op.get_bind(), checkfirst=True)
