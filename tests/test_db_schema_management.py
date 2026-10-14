"""
On non-SQLite databases the app must not run DDL at startup; Alembic owns the schema.
Runs only when TEST_DATABASE_URL points at Postgres (see tests/conftest.py).
"""
import logging

import pytest
from sqlalchemy import inspect


def _is_postgres():
    import os
    return bool(os.environ.get("TEST_DATABASE_URL"))


pytestmark = pytest.mark.skipif(not _is_postgres(), reason="requires TEST_DATABASE_URL (Postgres)")


def test_init_db_on_postgres_skips_ddl_and_leaves_alembic_schema_intact(client, caplog):
    import app.models as models

    assert models.db.engine.dialect.name == "postgresql"

    before = _schema_snapshot(models.db.engine)
    with caplog.at_level(logging.INFO, logger="imageopz.models"):
        models.db.init_db()
    assert any("Schema managed by Alembic" in r.getMessage() for r in caplog.records)
    assert _schema_snapshot(models.db.engine) == before

    insp = inspect(models.db.engine)
    assert "alembic_version" in insp.get_table_names()


def _schema_snapshot(engine):
    insp = inspect(engine)
    return {
        t: (
            [(c["name"], str(c["type"]), c["nullable"]) for c in insp.get_columns(t)],
            sorted(i["name"] for i in insp.get_indexes(t)),
        )
        for t in insp.get_table_names()
    }
