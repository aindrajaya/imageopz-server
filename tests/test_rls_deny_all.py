"""
Supabase exposes the `public` schema via its Data API, so every app table must have RLS enabled
with no policies (deny-all for anon/authenticated). Postgres only (TEST_DATABASE_URL).
"""
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires TEST_DATABASE_URL (Postgres)")


def test_every_app_table_has_rls_enabled_and_no_policies(client):
    import app.models as models

    with models.db.engine.connect() as conn:
        rls = dict(conn.execute(text(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r'"
        )).all())
        policies = conn.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")).scalar_one()

    app_tables = set(models.Base.metadata.tables)
    assert app_tables <= set(rls)
    assert {t: rls[t] for t in app_tables} == {t: True for t in app_tables}
    assert policies == 0


def test_anon_role_sees_no_rows_while_owner_does(client):
    import app.models as models

    email = f"rls_{uuid.uuid4().hex[:8]}@example.com"
    with models.db.engine.begin() as conn:
        conn.execute(text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF; "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF; "
            "END $$"
        ))
        conn.execute(text("GRANT SELECT ON users TO anon"))  # mimics Supabase default grants
        conn.execute(text("INSERT INTO users (id, email) VALUES (:id, :e)"), {"id": str(uuid.uuid4()), "e": email})

    def count_as(role):
        with models.db.engine.connect() as conn:
            try:
                if role:
                    conn.execute(text(f"SET ROLE {role}"))
                return conn.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": email}).scalar_one()
            finally:
                conn.execute(text("RESET ROLE"))
                conn.rollback()

    assert count_as(None) == 1       # owner sees the row (owners bypass RLS)
    assert count_as("anon") == 0     # RLS deny-all

    # Sanity: the check above is meaningful only if anon WOULD see the row without RLS.
    with models.db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE users DISABLE ROW LEVEL SECURITY"))
    try:
        assert count_as("anon") == 1
    finally:
        with models.db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ENABLE ROW LEVEL SECURITY"))
    assert count_as("anon") == 0


def test_alembic_version_is_rls_protected_from_anon(client):
    import app.models as models

    with models.db.engine.begin() as conn:
        conn.execute(text(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF; END $$"
        ))
        conn.execute(text("GRANT SELECT, UPDATE, DELETE ON alembic_version TO anon"))
        assert conn.execute(text(
            "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.alembic_version'::regclass"
        )).scalar_one() is True

    def as_anon(sql):
        conn = models.db.engine.connect()
        try:
            conn.execute(text("SET ROLE anon"))
            res = conn.execute(text(sql))
            return res.rowcount if res.returns_rows is False else res.scalar_one()
        finally:
            conn.execute(text("RESET ROLE"))
            conn.rollback()  # never persist anything from these probes
            conn.close()

    with models.db.engine.connect() as conn:
        owner_rows = conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one()
    assert owner_rows >= 1  # the owner sees the revision row

    assert as_anon("SELECT count(*) FROM alembic_version") == 0
    assert as_anon("UPDATE alembic_version SET version_num = version_num") == 0
    assert as_anon("DELETE FROM alembic_version") == 0

    # Sanity: without RLS the same anon statements WOULD hit the row (rolled back by as_anon).
    with models.db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY"))
    try:
        assert as_anon("SELECT count(*) FROM alembic_version") == owner_rows
        assert as_anon("UPDATE alembic_version SET version_num = version_num") == owner_rows
        assert as_anon("DELETE FROM alembic_version") == owner_rows
    finally:
        with models.db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY"))
    assert as_anon("DELETE FROM alembic_version") == 0

    with models.db.engine.connect() as conn:  # nothing was actually deleted
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == owner_rows
