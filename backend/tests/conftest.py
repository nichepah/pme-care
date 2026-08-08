"""Test fixtures — run against the real Postgres from docker-compose.

One-time setup:  docker compose -f ../infra/docker-compose.yml up -d db
Then simply:     pytest

Tests use the same dialect as production, so there are no cross-database
shims to maintain. AUTH_FAKE_MODE makes the bearer token the firebase_uid,
so no Firebase credentials or network are needed.

The suite runs in its own database, ``<configured name>_test``, created on
first use. It has to: the fixtures below TRUNCATE every table between tests and
drop the whole schema at the end, so pointing them at the database you develop
against would delete your data on every run. The *server* still comes from
DATABASE_URL — only the database name is swapped, so there is nothing extra to
configure.
"""

import os

os.environ["AUTH_FAKE_MODE"] = "true"
os.environ["ENV"] = "test"
# DATABASE_URL is intentionally NOT set here — it must come from your .env
# file (or a real exported env var). Setting a fallback here would take
# priority over .env, silently ignoring whatever database you configured.

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config import get_settings, settings

get_settings.cache_clear()

TEST_DB_SUFFIX = "_test"


def _redirect_to_test_database() -> None:
    """Point the process at ``<name>_test``, creating it if it is missing.

    Runs at import time, before ``app.db`` builds its engine, so every consumer
    — the session factory, ``alembic/env.py``, the schema-parity test — sees the
    test database and nothing has to be rebound afterwards.
    """
    url = make_url(settings.DATABASE_URL)
    if url.database is None or url.database.endswith(TEST_DB_SUFFIX):
        return
    target = f"{url.database}{TEST_DB_SUFFIX}"

    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                  {"n": target}).scalar_one_or_none()
            if exists is None:
                conn.execute(text(f'CREATE DATABASE "{target}"'))
    except OperationalError as exc:
        raise pytest.UsageError(
            f"Cannot reach the database server at {url.set(password=None)}. "
            "Start it first — see the README.") from exc
    except ProgrammingError as exc:
        raise pytest.UsageError(
            f"Cannot create the test database {target!r}: {exc}. Create it by hand, or point "
            f"DATABASE_URL at a database whose name already ends in {TEST_DB_SUFFIX!r}.") from exc
    finally:
        admin.dispose()

    # render_as_string(hide_password=False), never str(): SQLAlchemy's __str__
    # redacts the password to "***", which would be carried through as a literal
    # password and fail authentication.
    settings.DATABASE_URL = url.set(database=target).render_as_string(hide_password=False)


_redirect_to_test_database()

from app import db as db_module  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base, User, UserRole  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schema():
    """Create the schema once per test session, drop it afterwards."""
    with db_module.engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.commit()
    Base.metadata.create_all(db_module.engine)
    yield
    Base.metadata.drop_all(db_module.engine)


@pytest.fixture(autouse=True)
def clean_tables():
    """Truncate all tables between tests so each starts from empty."""
    yield
    with db_module.engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
        conn.commit()


@pytest.fixture()
def client() -> TestClient:
    """HTTP client over a freshly built app."""
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def db_session():
    """A direct database session for tests that need to set up state the
    API doesn't yet expose (e.g. linking a User to an Employee)."""
    with db_module.SessionLocal() as s:
        yield s


@pytest.fixture()
def make_user():
    """Insert a user; returns the bearer token (== firebase_uid in fake mode)."""

    def _make(role: UserRole = UserRole.EMPLOYEE, uid: str = "uid-1",
              active: bool = True) -> str:
        with db_module.SessionLocal() as s:
            s.add(User(firebase_uid=uid, email=f"{uid}@example.com", role=role,
                       display_name=f"User {uid}", is_active=active))
            s.commit()
        return uid

    return _make


@pytest.fixture()
def auth():
    """Build the Authorization header for a bearer token."""
    return lambda token: {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def register(auth):
    """Register an employee through the API and return the response body.

    Keyword arguments override individual fields, so a test only states the
    part of the payload it actually cares about.
    """

    def _register(client, token: str, personal_number: str = "P100", **overrides) -> dict:
        payload = {"personal_number": personal_number, "full_name": "R. Kumar",
                   "department": "Foundry", "plant": "Plant 2",
                   "contact_number": "9990001111"}
        payload.update(overrides)
        response = client.post("/api/v1/employees", headers=auth(token), json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    return _register
