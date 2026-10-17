from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, ScopeMixin, TimestampMixin, scope_xor, uuid_pk

FALL_RESPONSES = ("PENDING", "ACKNOWLEDGED", "DISPATCHED", "FALSE_ALARM")  # 대기중 / 확인함 / 출동중 / 오탐지
FALL_SOURCES = ("EDGE", "SIMULATED", "SEED")


class FallEvent(TimestampMixin, ScopeMixin, Base):
    """낙상 이벤트. resident_name/room/facility_id/owner_user_id 는 발생 시점 스냅샷 (ERD §1.1 '이력 소유권 스냅샷').

    거주자가 삭제돼도 이력은 남는다(resident_id SET NULL). 응답 전이는 event_logs 에 감사행으로 함께 기록한다.
    """

    __tablename__ = "fall_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    resident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("residents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resident_name: Mapped[str] = mapped_column(Text, nullable=False)
    room: Mapped[str] = mapped_column(Text, nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="SIMULATED", server_default=text("'SIMULATED'")
    )
    response: Mapped[str] = mapped_column(
        Text, nullable=False, default="PENDING", server_default=text("'PENDING'")
    )
    responded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Kafka 재전달/프로세스 재시작에도 같은 추론 전이가 두 이벤트가 되지 않게 한다.
    source_event_key: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        scope_xor(),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("duration_s >= 0", name="duration_nonneg"),
        CheckConstraint("source IN ('EDGE','SIMULATED','SEED')", name="source"),
        CheckConstraint(
            "response IN ('PENDING','ACKNOWLEDGED','DISPATCHED','FALSE_ALARM')", name="response"
        ),
        Index("ix_fall_events_facility_ts", "facility_id", text("occurred_at DESC")),
        Index("ix_fall_events_owner_ts", "owner_user_id", text("occurred_at DESC")),
    )
