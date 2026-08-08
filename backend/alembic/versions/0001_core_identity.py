"""0001 — core identity: extensions, user_role enum, users, audit_logs.

Revision ID: 0001
Revises: None
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

USER_ROLE = ("EMPLOYEE", "DOCTOR", "HEALTH_TEAM", "ADMIN")


def upgrade() -> None:
    """Create extensions, the user_role enum, users and audit_logs tables."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    user_role = postgresql.ENUM(*USER_ROLE, name="user_role")
    user_role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("firebase_uid", sa.String(128), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role", postgresql.ENUM(*USER_ROLE, name="user_role", create_type=False), nullable=False),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint("uq_users_firebase_uid", "users", ["firebase_uid"])
    # Partial unique: e-mail unique among live (not soft-deleted) rows only.
    op.create_index("uq_users_email_live", "users", ["email"], unique=True,
                    postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role", postgresql.ENUM(*USER_ROLE, name="user_role", create_type=False), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_actor_time", "audit_logs", ["actor_user_id", "created_at"])


def downgrade() -> None:
    """Drop everything created by this revision (dev convenience only)."""
    op.drop_table("audit_logs")
    op.drop_table("users")
    postgresql.ENUM(name="user_role").drop(op.get_bind(), checkfirst=True)
