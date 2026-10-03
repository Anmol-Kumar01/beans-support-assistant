"""Apply the SQL migrations in app/db/migrations/ in order, each exactly once.

  python -m app.db.migrate            apply pending migrations
  python -m app.db.migrate --status   list applied and pending migrations

Each file runs in its own transaction and is recorded in ``schema_migrations`` with a
checksum. If an already-applied file changes on disk, this stops with an error: add a new
numbered migration instead of editing an old one. Usually run by scripts/setup_db.sh.
"""

import argparse
import hashlib
import sys
from pathlib import Path

import psycopg

from app.core.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def _migrations() -> list[tuple[str, str, str]]:
    """(version, sql, checksum) for every migration file, in order."""
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        out.append((path.stem, sql, hashlib.sha256(sql.encode()).hexdigest()))
    return out


def _applied(conn: psycopg.Connection) -> dict[str, str]:
    conn.execute(_TRACKING)
    return dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())


def migrate(database_url: str, status_only: bool = False) -> int:
    with psycopg.connect(database_url, autocommit=True) as conn:
        applied = _applied(conn)
        pending = []
        for version, sql, checksum in _migrations():
            if version in applied:
                if applied[version] != checksum:
                    print(f"ERROR: {version}.sql changed after it was applied. Add a new migration instead.")
                    return 1
                print(f"  applied  {version}")
            else:
                pending.append((version, sql, checksum))
                print(f"  pending  {version}")
        if status_only or not pending:
            print("Database is up to date." if not pending else f"{len(pending)} migration(s) pending.")
            return 0
        for version, sql, checksum in pending:
            with conn.transaction():
                conn.execute(sql)
                conn.execute("INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", (version, checksum))
            print(f"  done     {version}")
    print(f"Applied {len(pending)} migration(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.db.migrate")
    p.add_argument("--status", action="store_true", help="show migrations without applying")
    args = p.parse_args(argv)
    url = get_settings().database_url
    try:
        return migrate(url, status_only=args.status)
    except psycopg.OperationalError as exc:
        print(f"Can't connect to the database ({url.split('@')[-1]}): {exc}".strip())
        print("Start it with ./scripts/setup_db.sh")
        return 2


if __name__ == "__main__":
    sys.exit(main())
