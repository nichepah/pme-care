"""0003 — bring the migrated schema level with app/models.py.

0001/0002 left three things declared in only one of the two places. Since
tests build the schema from ``Base.metadata`` and production builds it from
these revisions, any gap is a constraint or index that no test can ever
exercise. This revision closes the gap:

* FK employees.user_id -> users.id (the link was unique but unenforced)
* trigram indexes backing the ILIKE search in GET /employees (SRCH-1)
* the audit_logs.created_at index used to order GET /audit-logs

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the missing FK and the three missing indexes."""
    op.create_foreign_key("fk_employees_user_id", "employees", "users", ["user_id"], ["id"])

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index("ix_employees_full_name_trgm", "employees", ["full_name"],
                    postgresql_using="gin", postgresql_ops={"full_name": "gin_trgm_ops"})
    op.create_index("ix_employees_personal_number_trgm", "employees", ["personal_number"],
                    postgresql_using="gin", postgresql_ops={"personal_number": "gin_trgm_ops"})

    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    """Drop everything created by this revision."""
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_employees_personal_number_trgm", table_name="employees")
    op.drop_index("ix_employees_full_name_trgm", table_name="employees")
    op.drop_constraint("fk_employees_user_id", "employees", type_="foreignkey")
