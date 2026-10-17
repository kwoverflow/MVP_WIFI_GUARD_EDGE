"""wifiguard_db — WIFI-GUARD 관계형 스키마 (SQLAlchemy 2.x + Alembic).

스키마의 단일 진실원은 `models/` 이고, 마이그레이션은 `migrations/versions/` 에 있다.
설계 근거: DOCS/CSI-Guard_데이터모델_ERD_v1.0.docx + REVIEW_20260907.md §6 (문서↔코드 충돌 채택안).
"""

from .base import Base
from .engine import SessionLocal, engine, get_session
from .settings import settings

__all__ = ["Base", "SessionLocal", "engine", "get_session", "settings"]
