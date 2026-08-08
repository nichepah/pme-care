"""Database engine and request-scoped session dependency.

Small pool per instance (FT-2); pre-ping survives Neon free-tier auto-wake.
Tests point DATABASE_URL at the docker-compose Postgres — one dialect
everywhere, so what passes in tests is what runs in production.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.DATABASE_URL, pool_size=settings.DB_POOL_SIZE,
                       max_overflow=2, pool_pre_ping=True, pool_recycle=1800)

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
