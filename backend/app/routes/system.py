"""System endpoints: liveness probe and identity."""

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.config import settings
from app.db import get_db
from app.models import User, record_audit, utcnow
from app.schemas import MeResponse

router = APIRouter(tags=["system"])

SESSION_WINDOW = timedelta(minutes=30)
"""How long one ``last_login_at`` stamp stands for a single session."""


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Readiness: this instance can serve, database included.

    Use this for startup probes and load-balancer readiness. It touches the
    database on purpose — an instance that cannot reach Postgres should not
    receive traffic.
    """
    db.execute(text("SELECT 1"))
    return {"status": "ok", "service": settings.APP_NAME,
            "version": settings.APP_VERSION, "env": settings.ENV,
            # The interface reads this to decide whether to show the demo
            # banner. Reported by the server rather than configured in the
            # browser, so a demo instance cannot be made to look like a real one
            # by editing the page.
            "demo": settings.is_demo}


@router.get("/health/live")
def liveness() -> dict:
    """Liveness: the process is up. Deliberately does not touch the database.

    A liveness probe answers "should this container be killed?", and a database
    outage is not a reason to kill anything — restarting every instance during a
    Neon blip turns a brief dependency failure into an outage of its own, and the
    fresh instances cannot reach the database either. Readiness is what should
    react to the database; that is ``/health``.
    """
    return {"status": "alive", "version": settings.APP_VERSION}


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser = Depends(get_current_user),
       db: Session = Depends(get_db)) -> MeResponse:
    """Return the authenticated principal; the SPA calls this after login.

    This is also where a login gets recorded (AUTH-1/AUD-1). Firebase does the
    authenticating, so the backend never sees a sign-in event as such — the
    first authenticated call after one is the closest thing there is, and that
    call is this endpoint.

    Rate-limited to one stamp per ``SESSION_WINDOW`` so that a SPA polling
    ``/me`` writes one row per session rather than one per poll.
    """
    account = db.get(User, user.id)
    if account is not None:
        now = utcnow()
        if account.last_login_at is None or now - account.last_login_at > SESSION_WINDOW:
            account.last_login_at = now
            record_audit(db, user, "LOGIN", "user", account.id)
    return MeResponse(id=str(user.id), email=user.email,
                      display_name=user.display_name, role=user.role)
