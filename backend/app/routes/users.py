"""Account administration (AUTH-4, AUTH-7) — ADMIN only.

Before this existed the only way to create a Doctor or Health Team login was
``scripts/seed_dev.py``, and the only way to revoke one was an UPDATE by hand;
AUTH-7 (a deactivated account cannot authenticate) had no endpoint behind it.

Employee logins are not created here — they come from
``POST /employees/{id}/login`` so that each one stays tied to an employee
record.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import ConflictError, CurrentUser, ForbiddenError, NotFoundError, require_roles
from app.db import get_db
from app.lookups import flush_or_conflict, parse_uuid
from app.models import User, UserRole, record_audit, utcnow
from app.paging import PageParams, page_params, paginate
from app.provisioning import get_provisioner
from app.schemas import Page, UserCreate, UserCreatedOut, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

NOT_FOUND = "User not found."


def _to_out(u: User) -> UserOut:
    """Map a User row to its API representation (never exposes firebase_uid)."""
    return UserOut(id=str(u.id), email=u.email, display_name=u.display_name,
                   role=u.role, is_active=u.is_active, last_login_at=u.last_login_at)


def _load(db: Session, user_id: str) -> User:
    """Fetch a live account or raise 404."""
    account = db.execute(select(User).where(User.id == parse_uuid(user_id, NOT_FOUND),
                                            User.deleted_at.is_(None))).scalar_one_or_none()
    if account is None:
        raise NotFoundError(NOT_FOUND)
    return account


@router.post("", response_model=UserCreatedOut, status_code=201)
def create_user(body: UserCreate,
                user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
                db: Session = Depends(get_db)) -> UserCreatedOut:
    """Provision a staff account (Doctor / Health Team / Admin).

    Works in both modes. In production the identity is created in Firebase with
    no password and the response carries a sign-in link for the new user; in
    AUTH_FAKE_MODE the uid is synthesized and doubles as a working bearer token.
    Either way this service never handles a password.
    """
    clash = db.execute(select(User).where(User.email == body.email,
                                          User.deleted_at.is_(None))).scalar_one_or_none()
    if clash is not None:
        raise ConflictError(f"An account with e-mail {body.email} already exists.")

    identity = get_provisioner(body.role.value.lower().replace("_", "-")).create(
        db, email=body.email, display_name=body.display_name)
    account = User(firebase_uid=identity.firebase_uid, email=body.email, role=body.role,
                   display_name=body.display_name, created_by=user.id)
    db.add(account)
    flush_or_conflict(db)   # the e-mail pre-check above can be raced
    record_audit(db, user, "CREATE", "user", account.id, summary={"role": account.role.value})
    return UserCreatedOut(**_to_out(account).model_dump(),
                          dev_bearer_token=identity.dev_bearer_token,
                          sign_in_link=identity.sign_in_link)


@router.get("", response_model=Page[UserOut])
def list_users(role: UserRole | None = Query(None),
               is_active: bool | None = Query(None),
               params: PageParams = Depends(page_params),
               user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
               db: Session = Depends(get_db)) -> Page[UserOut]:
    """List accounts, by role and/or active state."""
    stmt = select(User).where(User.deleted_at.is_(None))
    if role is not None:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    return paginate(db, stmt.order_by(User.display_name, User.id), params, _to_out)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: str, user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
             db: Session = Depends(get_db)) -> UserOut:
    """One account."""
    return _to_out(_load(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdate,
                user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
                db: Session = Depends(get_db)) -> UserOut:
    """Rename an account, change its role, or activate/deactivate it (AUTH-7).

    An admin cannot deactivate or demote their own account: that is how a
    deployment ends up with nobody able to administer it, and it is always a
    mistake rather than an intent.
    """
    account = _load(db, user_id)
    changed = body.model_dump(exclude_unset=True)
    if not changed:
        return _to_out(account)
    if account.id == user.id and ({"role", "is_active"} & set(changed)):
        raise ForbiddenError("You cannot change your own role or active state.")
    if account.role == UserRole.EMPLOYEE and "role" in changed:
        raise ForbiddenError("An employee account's role cannot be changed here.")

    for field, value in changed.items():
        setattr(account, field, value)
    account.updated_by = user.id
    db.flush()
    record_audit(db, user, "UPDATE", "user", account.id,
                 summary={"fields_changed": sorted(changed)})
    return _to_out(account)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
                db: Session = Depends(get_db)) -> None:
    """Soft-delete an account. It stops authenticating immediately (both
    ``deleted_at`` and ``is_active`` are checked at login) while its audit
    trail keeps pointing at a row that still exists.
    """
    account = _load(db, user_id)
    if account.id == user.id:
        raise ForbiddenError("You cannot delete your own account.")
    account.deleted_at, account.deleted_by = utcnow(), user.id
    account.is_active = False
    record_audit(db, user, "SOFT_DELETE", "user", account.id)
    db.flush()
    # Local deactivation already blocks this API. Disabling the identity as well
    # stops a live session minting fresh ID tokens for anything else sharing the
    # Firebase project.
    get_provisioner().revoke(account.firebase_uid)
