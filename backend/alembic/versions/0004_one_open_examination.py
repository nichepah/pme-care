"""0004 — let the database enforce "one open examination per employee".

``schedule_examination`` checked for an open PME and then inserted. Two
concurrent requests both passed the check, so an employee could end up with two
SCHEDULED examinations and no single answer to "what is their current status?".
A partial unique index makes the invariant the database's rule, which no
interleaving can defeat.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the partial unique index, after clearing any existing duplicates."""
    # A database that already raced would fail the CREATE INDEX. Keep the
    # earliest open examination per employee and cancel the rest, so the
    # migration is safe to run against real data.
    op.execute("""
        UPDATE examinations SET status = 'CANCELLED',
               cancel_reason = 'Cancelled by migration 0004: duplicate open examination.',
               updated_at = now()
        WHERE status = 'SCHEDULED' AND id NOT IN (
            SELECT DISTINCT ON (employee_id) id FROM examinations
            WHERE status = 'SCHEDULED' ORDER BY employee_id, created_at, id
        )
    """)
    op.create_index("uq_examinations_one_open_per_employee", "examinations", ["employee_id"],
                    unique=True, postgresql_where=sa.text("status = 'SCHEDULED'"))


def downgrade() -> None:
    """Drop the index (the cancellations above are not reversible)."""
    op.drop_index("uq_examinations_one_open_per_employee", table_name="examinations")
