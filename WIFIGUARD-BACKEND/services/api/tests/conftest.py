"""pytest 공통 픽스처.

- 테스트 DB: DATABASE_URL_TEST (기본 로컬 wg-pg 의 wifiguard_test). 없으면 만든다.
- 세션당 1회 `alembic upgrade head`, 테스트마다 전 테이블 TRUNCATE (alembic_version 제외).
- wifiguard_db.settings 는 import 시점에 환경을 읽으므로 어떤 wifiguard 모듈보다 먼저 DATABASE_URL 을 덮어쓴다.
"""

from __future__ import annotations

import os
from pathlib import Path

TEST_URL = os.environ.get(
    "DATABASE_URL_TEST", "postgresql+psycopg://wifiguard:devpass@127.0.0.1:5432/wifiguard_test"
)
os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-prod-0123456789abcdef")  # >= 32 bytes (RFC 7518)
os.environ.setdefault("ALLOW_FALL_SIMULATE", "1")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = BACKEND_ROOT / "packages" / "db" / "alembic.ini"


def _ensure_database(url: str) -> None:
    u = make_url(url)
    admin = u.set(database="postgres")
    eng = create_engine(admin, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        exists = conn.execute(text("select 1 from pg_database where datname = :n"), {"n": u.database}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{u.database}"'))
    eng.dispose()


@pytest.fixture(scope="session")
def alembic_cfg() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    return cfg


@pytest.fixture(scope="session")
def migrated_db(alembic_cfg: Config) -> str:
    _ensure_database(TEST_URL)
    command.upgrade(alembic_cfg, "head")
    return TEST_URL


@pytest.fixture(scope="session")
def engine(migrated_db: str):
    from wifiguard_db.engine import engine as _engine

    return _engine


@pytest.fixture()
def client(migrated_db):
    """FastAPI TestClient — 앱의 get_db 는 SessionLocal(테스트 DB)을 그대로 쓴다."""
    from fastapi.testclient import TestClient

    from wifiguard_api.app import app

    with TestClient(app) as c:
        yield c


def _truncate_all(engine) -> None:
    from wifiguard_db import Base

    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def _clean_tables(engine):
    """모든 테스트 전후로 전 테이블 TRUNCATE — client 만 쓰는 테스트도 격리된다."""
    _truncate_all(engine)
    yield
    _truncate_all(engine)


@pytest.fixture()
def db(engine):
    """트랜잭션 없는 일반 세션 (테스트에서 직접 DB 를 들여다볼 때)."""
    from wifiguard_db.engine import SessionLocal

    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()
