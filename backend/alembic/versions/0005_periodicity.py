"""0005 — record when the next examination falls due.

Until now nothing in the schema said when an employee's next PME was expected,
which is the one thing the word "periodic" is about: the system could record
examinations but could not answer "who is overdue?". Every completed
examination now carries the date the following one becomes due.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add next_due_date, and the index the compliance query reads it through."""
    op.add_column("examinations", sa.Column("next_due_date", sa.Date(), nullable=True))
    # The due/overdue worklist scans completed examinations by due date; without
    # this it is a full scan of the whole examination history every time.
    op.create_index("ix_examinations_next_due", "examinations", ["next_due_date"],
                    postgresql_where=sa.text("status = 'COMPLETED'"))


def downgrade() -> None:
    """Drop the column and its index."""
    op.drop_index("ix_examinations_next_due", table_name="examinations")
    op.drop_column("examinations", "next_due_date")
