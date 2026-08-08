"""Authentication, authorization, and the application error types.

Request flow: extract Bearer token → verify as Firebase ID token (Google's
cached public keys) → resolve the users row by firebase_uid → inject
CurrentUser. ``require_roles(...)`` enforces the endpoint role matrix
(deny-by-default); object-level scope checks live in route/service code.

AUTH_FAKE_MODE (dev/tests only): skips verification and treats the raw
bearer value as the firebase_uid, so the full stack runs offline.
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User, UserRole


class AppError(Exception):
    """Base error rendered as the standard envelope (API spec §1.5)."""

    status_code, code = 500, "INTERNAL_ERROR"

    def __init__(self, message: str, details: list[dict] | None = None):
        """Build an error carrying a client-safe message and field details."""
        super().__init__(message)
        self.message, self.details = message, details or []


class UnauthenticatedError(AppError):
    """401 — missing/expired/invalid credentials."""

    status_code, code = 401, "UNAUTHENTICATED"


class ForbiddenError(AppError):
    """403 — role does not permit the action."""

    status_code, code = 403, "FORBIDDEN"


class NotFoundError(AppError):
    """404 — absent, soft-deleted, or out-of-scope (anti-enumeration)."""

    status_code, code = 404, "NOT_FOUND"


class ConflictError(AppError):
    """409 — uniqueness/state conflict (duplicate PN, open exam exists)."""

    status_code, code = 409, "CONFLICT"


class BusinessRuleError(AppError):
    """422 — well-formed request violating a domain rule (e.g. DOC-6)."""

    status_code, code = 422, "BUSINESS_RULE_VIOLATION"


_firebase_ready = False


def init_firebase() -> None:
    """Initialize the Firebase Admin SDK once (skipped in fake mode)."""
    global _firebase_ready
    if _firebase_ready or settings.AUTH_FAKE_MODE:
        return
    import firebase_admin

    if not firebase_admin._apps:  # pragma: no cover
        firebase_admin.initialize_app(options={"projectId": settings.FIREBASE_PROJECT_ID})
    _firebase_ready = True


def _verify_bearer(request: Request) -> str:
    """Extract and verify the Bearer token; return the Firebase uid.

    Raises:
        UnauthenticatedError: header missing/malformed or token invalid.

    """
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthenticatedError("Missing or malformed Authorization header.")
    token = token.strip()
    if settings.AUTH_FAKE_MODE:
        return token
    from firebase_admin import auth as fb_auth

    try:
        return fb_auth.verify_id_token(token)["uid"]
    except Exception as exc:
        raise UnauthenticatedError("Invalid or expired credentials.") from exc


@dataclass(frozen=True)
class CurrentUser:
    """Authenticated principal injected into routes."""

    id: uuid.UUID
    role: UserRole
    display_name: str
    email: str


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    """Resolve the authenticated application user or raise 401.

    Unknown and inactive accounts get the same message as bad tokens so the
    response never reveals whether an account exists.
    """
    uid = _verify_bearer(request)
    user = db.execute(select(User).where(User.firebase_uid == uid,
                                         User.deleted_at.is_(None))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise UnauthenticatedError("Invalid or expired credentials.")
    return CurrentUser(id=user.id, role=user.role,
                       display_name=user.display_name, email=user.email)


def require_roles(*roles: UserRole):
    """Dependency factory allowing only the given roles (deny-by-default)."""

    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        """Enforce the role whitelist for this route."""
        if user.role not in roles:
            raise ForbiddenError("You do not have permission to perform this action.")
        return user

    return checker
