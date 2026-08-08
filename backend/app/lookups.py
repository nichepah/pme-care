"""Small helpers shared by the route modules: id parsing and conflict handling.

Both exist to stop the same few lines being re-typed (and drifting) in every
router.
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import ConflictError, NotFoundError

CONSTRAINT_CONFLICTS = {
    "uq_employees_personal_number_live": "An employee with that personal number already exists.",
    "uq_examinations_one_open_per_employee": "Employee already has an open examination.",
    "uq_users_email_live": "An account with that e-mail address already exists.",
    "uq_users_firebase_uid": "That account already exists.",
    "uq_employees_user_id": "That login is already linked to another employee.",
}
"""Unique constraints a client can legitimately collide with, and what to say.

Anything absent falls back to a generic 409 — still the right status, since a
unique violation is by definition a conflict rather than a server fault.
"""


def parse_uuid(value: str, not_found_message: str) -> uuid.UUID:
    """Parse a path/query id, or raise 404.

    A malformed id and an id that does not exist get the same answer on
    purpose: the caller learns nothing about which ids are real, and a typo in
    a URL can never surface as a 500 (API spec §1.5).
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(not_found_message) from exc


def flush_or_conflict(db: Session) -> None:
    """Flush pending changes, turning a unique violation into a clean 409.

    Every "does this already exist?" check in this codebase is a SELECT followed
    by an INSERT, which two concurrent requests can interleave. The database
    still refuses the duplicate — that is what the unique indexes are for — but
    without this the loser of the race gets a 500 from an unhandled
    ``IntegrityError`` while the sequential path returns a tidy 409. Same
    outcome, same status code, whichever way the timing falls.

    Raises:
        ConflictError: the flush hit a unique constraint.

    """
    try:
        db.flush()
    except IntegrityError as exc:
        # psycopg exposes the violated constraint; fall back to scanning the
        # message if a driver ever stops filling the diagnostics in.
        name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if name is None:
            name = next((c for c in CONSTRAINT_CONFLICTS if c in str(exc.orig)), None)
        raise ConflictError(CONSTRAINT_CONFLICTS.get(
            name, "That change conflicts with an existing record.")) from exc
