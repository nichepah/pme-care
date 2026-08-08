"""Audit trail read endpoint (AUD-1/AUD-2) — ADMIN only.

The trail was already being written on every change; without a way to read it
back, the requirement was only half met. It stays append-only: there is no
create, update or delete here by design.

Rows carry field names and technical metadata only, never clinical values
(SEC-8), so exposing them to an administrator does not expose medical data.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_roles
from app.db import get_db
from app.lookups import parse_uuid
from app.models import AuditLog, UserRole
from app.paging import PageParams, page_params, paginate
from app.schemas import AuditLogOut, Page

router = APIRouter(prefix="/audit-logs", tags=["audit"])


def _to_out(row: AuditLog) -> AuditLogOut:
    """Map an AuditLog row to its API representation.

    ``ip_address`` needs the explicit ``str()``: psycopg 3 reads an ``INET``
    column back as an ``ipaddress.IPv4Address``/``IPv6Address`` object, not a
    string, so passing it through unconverted fails response validation.
    """
    return AuditLogOut(id=row.id, actor_user_id=str(row.actor_user_id) if row.actor_user_id else None,
                       actor_role=row.actor_role, action=row.action, entity_type=row.entity_type,
                       entity_id=str(row.entity_id) if row.entity_id else None,
                       ip_address=str(row.ip_address) if row.ip_address else None,
                       summary=row.summary, created_at=row.created_at)


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(entity_type: str | None = Query(None, max_length=60),
                    entity_id: str | None = Query(None, description="Requires entity_type to be useful"),
                    actor_user_id: str | None = Query(None),
                    action: str | None = Query(None, max_length=40),
                    since: datetime | None = Query(None, description="Inclusive lower bound on created_at"),
                    until: datetime | None = Query(None, description="Inclusive upper bound on created_at"),
                    params: PageParams = Depends(page_params),
                    user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
                    db: Session = Depends(get_db)) -> Page[AuditLogOut]:
    """Newest-first audit rows, filtered by entity, actor, action or time window.

    The common use is "everything that ever happened to this record": pass
    ``entity_type=employee&entity_id=<uuid>``.
    """
    stmt = select(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == parse_uuid(entity_id, "Audit entry not found."))
    if actor_user_id:
        stmt = stmt.where(AuditLog.actor_user_id == parse_uuid(actor_user_id, "Audit entry not found."))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at <= until)
    return paginate(db, stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()),
                    params, _to_out)
