"""The migrations and the models must describe the same database.

Tests build the schema with ``Base.metadata.create_all``; production builds it
by running the Alembic revisions. Anything present in only one of the two is
either a constraint no test can ever exercise, or one that fails in production
after passing locally. This module migrates a scratch database and compares it
to the models, so that gap cannot reopen unnoticed.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from alembic import command
from alembic.config import Config
from app import db as db_module
from app.config import settings
from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRATCH_SUFFIX = "_alembic_parity"

# Compared per table. Column *types* are deliberately not compared: SQLAlchemy
# and Alembic spell the same Postgres type in different ways often enough that
# it produces noise rather than findings, and a wrong type shows up as a
# failing behavioural test instead.
COMPARED = ("columns", "indexes", "foreign_keys", "unique_constraints")


@pytest.fixture(scope="module")
def migrated_url() -> str:
    """A scratch database with every migration applied, dropped afterwards."""
    url = make_url(settings.DATABASE_URL)
    scratch = f"{url.database}{SCRATCH_SUFFIX}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}"'))
            conn.execute(text(f'CREATE DATABASE "{scratch}"'))
    except ProgrammingError as exc:  # e.g. a Neon role without CREATEDB
        pytest.skip(f"cannot create a scratch database: {exc}")

    scratch_url = str(url.set(database=scratch))
    original = settings.DATABASE_URL
    try:
        # alembic/env.py reads the URL off the settings singleton.
        settings.DATABASE_URL = scratch_url
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(config, "head")
        yield scratch_url
    finally:
        settings.DATABASE_URL = original
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}"'))
        admin.dispose()


def _describe(engine) -> dict:
    """Reduce a live schema to comparable sets of names per table."""
    inspector = inspect(engine)
    described = {}
    for table in sorted(set(inspector.get_table_names()) - {"alembic_version"}):
        described[table] = {
            "columns": {c["name"] for c in inspector.get_columns(table)},
            "indexes": {i["name"] for i in inspector.get_indexes(table)},
            "foreign_keys": {tuple(fk["constrained_columns"]) + (fk["referred_table"],)
                             for fk in inspector.get_foreign_keys(table)},
            "unique_constraints": {tuple(u["column_names"])
                                   for u in inspector.get_unique_constraints(table)},
        }
    return described


def test_migrated_schema_matches_the_models(migrated_url):
    """Every table, column, index, FK and unique constraint must match.

    The ``schema`` fixture has already created the model schema in the main
    test database, so this compares two live databases rather than a database
    against metadata — the same thing Alembic's autogenerate would see, minus
    its type-spelling noise.
    """
    migrated = create_engine(migrated_url)
    try:
        from_migrations = _describe(migrated)
    finally:
        migrated.dispose()
    from_models = _describe(db_module.engine)

    assert set(from_migrations) == set(from_models), "table sets differ"
    for table in from_models:
        for aspect in COMPARED:
            assert from_migrations[table][aspect] == from_models[table][aspect], (
                f"{table}.{aspect} differs between migrations and models: "
                f"only in migrations={from_migrations[table][aspect] - from_models[table][aspect]}, "
                f"only in models={from_models[table][aspect] - from_migrations[table][aspect]}")


def test_every_model_table_is_in_the_migrated_schema(migrated_url):
    """A new model with no migration is the most common way to break deploys."""
    migrated = create_engine(migrated_url)
    try:
        migrated_tables = set(inspect(migrated).get_table_names())
    finally:
        migrated.dispose()
    assert set(Base.metadata.tables) <= migrated_tables
