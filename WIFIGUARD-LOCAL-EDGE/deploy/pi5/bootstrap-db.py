"""Provision NEW native Pi5 databases only; no drop/truncate/ALTER existing roles.

Run as PostgreSQL administrator with a private copy of the Pi5 env file. Peer
socket is the default; an authenticated PI5_DB_ADMIN_DSN can override it.
"""

import argparse
import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    config = dict(
        line.split("=", 1)
        for line in args.config.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    from sqlalchemy.engine import make_url

    url = make_url(config["DATABASE_URL"])
    if (
        url.username != "wifiguard_pi5"
        or url.database != "wifiguard_pi5"
        or url.host != "127.0.0.1"
    ):
        raise ValueError(
            "bootstrap only accepts the isolated wifiguard_pi5 local profile"
        )
    if not url.password or "REPLACE" in url.password:
        raise ValueError("generate private settings first")
    admin = os.getenv("PI5_DB_ADMIN_DSN", "dbname=postgres host=/var/run/postgresql")
    names = ("wifiguard_pi5", "wifiguard_pi5_ts")
    with psycopg.connect(admin, autocommit=True) as connection:
        if connection.execute("SHOW server_version_num").fetchone()[0][:2] != "16":
            raise RuntimeError("this profile is validated with PostgreSQL 16")
        available = connection.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'"
        ).fetchone()
        preloaded = (
            connection.execute("SHOW shared_preload_libraries").fetchone()[0].split(",")
        )
        if not available or "timescaledb" not in [item.strip() for item in preloaded]:
            raise RuntimeError(
                "install and preload native TimescaleDB first; no databases changed"
            )
        if connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (url.username,)
        ).fetchone():
            raise RuntimeError(
                "Pi5 role already exists; refusing to overwrite credentials or existing data"
            )
        for name in names:
            if connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone():
                raise RuntimeError(
                    "Pi5 database already exists; refusing to replace it"
                )
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(url.username), sql.Literal(url.password)
            )
        )
        for name in names:
            connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(url.username)
                )
            )
    root = Path(__file__).resolve().parents[3]
    init = root / "WIFIGUARD-BACKEND/deploy/aws/init-timescale.sql"
    # Extension creation requires the native Timescale package to be installed
    # and preloaded. Fail loudly; never silently substitute an ordinary table.
    with psycopg.connect(
        make_conninfo(admin, dbname=names[1]), autocommit=True
    ) as connection:
        connection.execute(init.read_text(encoding="utf-8"), prepare=False)
        connection.execute(
            sql.SQL("ALTER TABLE presence_samples OWNER TO {}").format(
                sql.Identifier(url.username)
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(url.username)
            )
        )
    print(
        "PI5_DATABASES_CREATED: run existing Alembic migrations with private DATABASE_URL next"
    )


if __name__ == "__main__":
    main()
