"""서비스 API 설정 — 환경변수에서 읽는다 (compose/.env.example 참조)."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"  # main.py:54 와 동일 정책
DEV_JWT_SECRET = "changeme"  # .env.example 의 placeholder — 운영에서는 반드시 교체


@dataclass(frozen=True)
class ApiSettings:
    jwt_secret: str
    access_ttl_min: int
    refresh_ttl_days: int
    cors_origin_regex: str
    allow_fall_simulate: bool

    @classmethod
    def from_env(cls) -> "ApiSettings":
        return cls(
            jwt_secret=os.environ.get("JWT_SECRET", DEV_JWT_SECRET),
            access_ttl_min=int(os.environ.get("JWT_ACCESS_TTL_MIN", "15")),
            refresh_ttl_days=int(os.environ.get("JWT_REFRESH_TTL_DAYS", "14")),
            cors_origin_regex=os.environ.get("CORS_ORIGIN_REGEX", DEFAULT_CORS_ORIGIN_REGEX),
            allow_fall_simulate=os.environ.get("ALLOW_FALL_SIMULATE", "1") == "1",
        )

    @property
    def jwt_secret_is_default(self) -> bool:
        return self.jwt_secret == DEV_JWT_SECRET

    @property
    def jwt_secret_bytes(self) -> int:
        return len(self.jwt_secret.encode("utf-8"))

    def validate_security(self) -> None:
        """Reject explicitly configured JWT keys that are too short for HS256."""
        if not self.jwt_secret_is_default and self.jwt_secret_bytes < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 bytes")


settings = ApiSettings.from_env()
