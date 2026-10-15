# Runbook: throwaway local Postgres (Postgres.app binaries)

Read when you need the Postgres test leg and Docker is not installed. Never touch Postgres.app's (or Homebrew's) default cluster or its config.

1. `PGBIN=/Applications/Postgres.app/Contents/Versions/latest/bin; PGDIR=$(mktemp -d)`
   Homebrew instead: `PGBIN=/opt/homebrew/bin` (PostgreSQL 16.15 used in T-006b). Keep `PGDIR` short (plain `mktemp -d`):
   macOS limits Unix socket paths to 103 bytes, and a long directory makes the server exit at start.
2. `$PGBIN/initdb -D "$PGDIR/pg" -U postgres --auth=trust --locale=C -E UTF8` (an explicit locale avoids the Homebrew
   "could not find suitable text search configuration" warning)
3. `$PGBIN/pg_ctl -D "$PGDIR/pg" -o "-p 5433 -k $PGDIR" -l "$PGDIR/log" -w start`
4. `$PGBIN/createdb -h localhost -p 5433 -U postgres imageopz_dev`
5. Tests: `TEST_DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5433/postgres pytest -q` (conftest creates and drops a fresh database).
6. Stop and delete when done: `$PGBIN/pg_ctl -D "$PGDIR/pg" stop; rm -rf "$PGDIR"`. Confirm nothing listens on 5433.

Record the Postgres version (`$PGBIN/postgres --version`) in task handoffs.
