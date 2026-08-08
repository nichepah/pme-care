"""Small helpers shared by the route modules: id parsing and the dev-mode
account-creation shim.

Both exist to stop the same few lines being re-typed (and drifting) in every
router.
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import BusinessRuleError, NotFoundError
from app.config import settings
from app.models import User


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


def require_dev_account_mode(what: str) -> None:
    """Refuse synthetic account creation outside AUTH_FAKE_MODE.

    Accounts here are created by inventing a ``firebase_uid`` instead of
    calling the Firebase Admin SDK. That is fine for local development and
    tests, and must never happen against a real project — an invented uid
    matches no Firebase account, so nobody could actually sign in with it.

    Raises:
        BusinessRuleError: when AUTH_FAKE_MODE is off.

    """
    if not settings.AUTH_FAKE_MODE:
        raise BusinessRuleError(
            f"Dev-mode {what} is disabled outside AUTH_FAKE_MODE. Real Firebase "
            "account creation (invite + sign-in link) is not implemented yet.")


def unique_dev_uid(db: Session, prefix: str, seed: str) -> str:
    """Build a readable, unused synthetic ``firebase_uid``.

    Example: prefix ``emp`` + seed ``"P-1001"`` -> ``emp-p-1001``. A short
    random suffix is appended only if that is already taken.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", seed.strip().lower()).strip("-") or "account"
    uid = f"{prefix}-{slug}"[:120]
    if db.execute(select(User).where(User.firebase_uid == uid)).scalar_one_or_none() is not None:
        uid = f"{uid}-{uuid.uuid4().hex[:6]}"
    return uid
