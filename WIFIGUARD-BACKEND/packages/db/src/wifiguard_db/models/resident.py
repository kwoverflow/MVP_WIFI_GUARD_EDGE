from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, ScopeMixin, TimestampMixin, scope_xor, uuid_pk


class Resident(TimestampMixin, ScopeMixin, Base):
    """거주자(FACILITY 입소자 / HOME 재실 대상). 런타임 필드(state, mv, wander, presence, …)는 컬럼이 아니다 (D2).

    기기 매핑은 resident_devices(N:M) 이며 주 장치는 is_primary 행 하나로 표현한다 (ERD §1.1 '주 매핑 + 다중 매핑').
    """

    __tablename__ = "residents"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    room: Mapped[str] = mapped_column(Text, nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caregiver: Mapped[str | None] = mapped_column(Text, nullable=True)  # FACILITY 담당 요양사 / HOME 관계·메모
    threshold_override: Mapped[float | None] = mapped_column(Float, nullable=True)  # MV 임계값 개별 오버라이드

    device_links: Mapped[list["ResidentDevice"]] = relationship(
        back_populates="resident", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        scope_xor(),
        CheckConstraint("age IS NULL OR (age >= 0 AND age <= 150)", name="age_range"),
    )

    @property
    def device_ids(self) -> list[uuid.UUID]:
        return [link.device_id for link in self.device_links]

    @property
    def primary_device_id(self) -> uuid.UUID | None:
        for link in self.device_links:
            if link.is_primary:
                return link.device_id
        return None


class ResidentDevice(Base):
    """거주자↔기기 매핑. 거주자당 is_primary 는 최대 1행 (부분 유니크 인덱스)."""

    __tablename__ = "resident_devices"

    resident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("residents.id", ondelete="CASCADE"), primary_key=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    resident: Mapped[Resident] = relationship(back_populates="device_links")

    __table_args__ = (
        Index("ux_resident_devices_primary", "resident_id", unique=True, postgresql_where=text("is_primary")),
    )
