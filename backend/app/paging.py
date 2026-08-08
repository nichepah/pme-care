"""Offset pagination shared by every list endpoint (API spec §1.4).

One helper so that page/size validation, the ``total`` count, and the response
envelope are identical everywhere instead of re-derived per route.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.schemas import Page

TRow = TypeVar("TRow")
TOut = TypeVar("TOut")

DEFAULT_SIZE = 20
MAX_SIZE = 100
"""Hard cap on rows per request. Must stay <= the bound on ``Page.size``, which
validates the response — raising it here alone would turn a large page into a
500 instead of a bigger page."""


@dataclass(frozen=True)
class PageParams:
    """Validated pagination request."""

    page: int
    size: int

    @property
    def offset(self) -> int:
        """Rows to skip for this page."""
        return (self.page - 1) * self.size


def page_params(page: int = Query(1, ge=1, description="1-based page number"),
                size: int = Query(DEFAULT_SIZE, ge=1, le=MAX_SIZE,
                                  description="Rows per page")) -> PageParams:
    """Dependency supplying ``?page=&size=`` for list endpoints."""
    return PageParams(page=page, size=size)


def paginate(db: Session, stmt: Select, params: PageParams,
             to_out: Callable[[TRow], TOut]) -> Page[TOut]:
    """Run ``stmt`` as one page and wrap the rows in the standard envelope.

    Two queries: an unordered ``COUNT(*)`` over the same filters, then the
    page itself. ``total`` is the count of all matching rows, not of this page,
    so a client can compute how many pages exist.

    Args:
        db: Active session.
        stmt: A fully filtered and ordered ``select()`` of one ORM entity.
        params: Page/size from ``page_params``.
        to_out: Maps one ORM row to its response model.

    """
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(stmt.limit(params.size).offset(params.offset)).scalars().all()
    return Page(items=[to_out(row) for row in rows], total=total,
                page=params.page, size=params.size)
