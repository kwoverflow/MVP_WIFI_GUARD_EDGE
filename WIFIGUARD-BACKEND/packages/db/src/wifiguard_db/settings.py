"""DB 접속 설정.

우선순위: DATABASE_URL > PG_DSN(health_app 과 공유, 드라이버 접두를 psycopg 로 변환) > 로컬 드라이런 기본값.
로컬 기본값은 개발 PC 의 `wg-pg` 컨테이너(deploy/aws/README.md 드라이런)와 같다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "postgresql+psycopg://wifiguard:devpass@127.0.0.1:5432/wifiguard"


def _to_sqlalchemy_url(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://") :]
    return dsn


@dataclass(frozen=True)
class Settings:
    database_url: str
    echo_sql: bool

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("DATABASE_URL") or os.environ.get("PG_DSN") or DEFAULT_DATABASE_URL
        return cls(
            database_url=_to_sqlalchemy_url(url),
            echo_sql=os.environ.get("SQL_ECHO", "0") == "1",
        )


settings = Settings.from_env()
