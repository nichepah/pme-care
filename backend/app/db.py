"""Database engine and request-scoped session dependency.

Small pool per instance (FT-2); pre-ping replaces a connection that died while a
managed Postgres was suspended, rather than raising.

Tests point DATABASE_URL at a real Postgres — one dialect everywhere, so what
passes in tests is what runs in production.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Hosted Postgres is usually reached through a transaction-mode connection
# pooler (Supabase's pgbouncer on :6543, Neon's pooled endpoint). Those do not
# keep a session between statements, so a server-side prepared statement from
# one checkout is gone by the next and psycopg fails with "prepared statement
# already exists" / "does not exist" under load. Disabling the prepare cache
# costs a little planning time and is the documented way to use them.
_POOLER_MARKERS = ("pooler.supabase.com", "pgbouncer=true", "-pooler.")
_through_pooler = any(marker in settings.DATABASE_URL for marker in _POOLER_MARKERS)

_connect_args: dict = {}
if _through_pooler:
    _connect_args["prepare_threshold"] = None

engine = create_engine(settings.DATABASE_URL, pool_size=settings.DB_POOL_SIZE,
                       max_overflow=2, pool_pre_ping=True, pool_recycle=1800,
                       connect_args=_connect_args)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield a session; commit on success, roll back on error, always close."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
