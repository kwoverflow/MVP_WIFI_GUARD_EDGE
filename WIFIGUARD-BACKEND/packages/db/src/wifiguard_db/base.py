"""공통 베이스·믹스인.

- id: uuid, 서버 기본값 gen_random_uuid() (PG13+ 내장, 확장 불필요). 앱에서도 uuid4 를 넣을 수 있다.
- enum: PG enum 타입 대신 text + CHECK (ALTER TYPE 제약을 피하고 autogenerate 를 단순하게).
- 스코프 XOR: 서비스 격리 규칙 — (facility_id IS NOT NULL) <> (owner_user_id IS NOT NULL). ERD §4.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def uuid_fk(target: str, *, ondelete: str, nullable: bool = True, **kw) -> Mapped[uuid.UUID | None]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, **kw)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


SCOPE_XOR_SQL = "(facility_id IS NOT NULL) <> (owner_user_id IS NOT NULL)"


def scope_xor() -> CheckConstraint:
    """테이블별 `__table_args__` 에 넣는다. 이름은 naming_convention 으로 ck_<table>_scope_xor."""
    return CheckConstraint(SCOPE_XOR_SQL, name="scope_xor")


class ScopeMixin:
    """서비스 스코프 — FACILITY 는 facility_id, HOME 은 owner_user_id 중 정확히 하나."""

    @declared_attr
    def facility_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(
            UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), nullable=True, index=True
        )

    @declared_attr
    def owner_user_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(
            UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
        )
