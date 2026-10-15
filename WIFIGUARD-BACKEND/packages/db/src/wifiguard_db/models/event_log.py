from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, ScopeMixin, scope_xor

LOG_LEVELS = ("INFO", "WARN", "ERROR", "FALL")


class EventLog(ScopeMixin, Base):
    """이벤트 로그 = 응답 감사 이력. append-only (수정·삭제 API 없음).

    ERD 는 ts 를 PK 로 두었으나 밀리초 충돌 위험이 있어 bigserial id + (scope, ts DESC) 인덱스로 바꿨다.
    모든 로그는 테넌트 스코프를 가진다 — 전역(무스코프) 시스템 로그는 없다 (REVIEW §6 채택안 ③).
    """

    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    level: Mapped[str] = mapped_column(Text, nullable=False)
    msg: Mapped[str] = mapped_column(Text, nullable=False)
    resident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("residents.id", ondelete="SET NULL"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    fall_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fall_events.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        scope_xor(),
        CheckConstraint("level IN ('INFO','WARN','ERROR','FALL')", name="level"),
        Index("ix_event_logs_facility_ts", "facility_id", text("ts DESC")),
        Index("ix_event_logs_owner_ts", "owner_user_id", text("ts DESC")),
    )
