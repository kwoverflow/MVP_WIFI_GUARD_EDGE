from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin, scope_xor, uuid_pk

# 기본값은 백엔드 실값(RASPBERRY config/default.toml · state_machine.py)이며 프론트 목업 값이 아니다.
DEFAULTS = {
    "presence_mv_threshold": 2.0,
    "wander_ratio_threshold": 1.8,
    "presence_timeout_s": 10.0,
    "threshold": 0.468,
    "cooldown_seconds": 10.0,
}


class TenantConfig(TimestampMixin, Base):
    """테넌트(시설 1 / HOME 사용자 1)당 감지 설정. 세 축의 임계값을 섞지 않는다 (README '세 축의 임계값').

    - presence_mv_threshold / wander_ratio_threshold / presence_timeout_s : 재실(엣지) 파라미터
    - threshold / cooldown_seconds : 낙상 모델(클라우드) 파라미터
    """

    __tablename__ = "tenant_configs"

    id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    presence_mv_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=2.0, server_default=text("2.0")
    )
    wander_ratio_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.8, server_default=text("1.8")
    )
    presence_timeout_s: Mapped[float] = mapped_column(
        Float, nullable=False, default=10.0, server_default=text("10.0")
    )
    threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.468, server_default=text("0.468"))
    cooldown_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, default=10.0, server_default=text("10.0")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        scope_xor(),
        CheckConstraint("wander_ratio_threshold >= 1.0", name="wander_ratio_min"),
        CheckConstraint("threshold >= 0 AND threshold <= 1", name="threshold_range"),
        CheckConstraint("presence_mv_threshold > 0", name="mv_threshold_positive"),
        CheckConstraint("presence_timeout_s > 0", name="timeout_positive"),
        CheckConstraint("cooldown_seconds >= 0", name="cooldown_nonneg"),
    )
