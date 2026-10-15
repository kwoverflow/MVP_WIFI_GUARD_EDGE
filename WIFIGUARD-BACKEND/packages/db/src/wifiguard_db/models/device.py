from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, ScopeMixin, TimestampMixin, scope_xor, uuid_pk

CONNECTIONS = ("MQTT", "SERIAL", "SPI")
# 현행 코드의 61초 4단계 캘리브레이션 스테이지 (mock-store.ts CalibrationStage / backend CalibrationPhase 대문자)
CALIBRATION_STAGES = ("IDLE", "LEAVING", "WAITING_ACK", "WAITING_AGC", "MEASURING", "DONE", "ERROR")


class Device(TimestampMixin, ScopeMixin, Base):
    """기기. connection=MQTT → mqtt_topic 필수(서버 발급 `wifiguard/{scope}/{device_id}`), SERIAL → serial_port 필수.

    online/last_seen_at/RSSI 계열은 텔레메트리 필드 — 실시간 경로가 붙기 전까지는 시드·PATCH 값만 반영된다.
    presence_mv_threshold / wander_baseline 은 캘리브레이션 산출값(재실 축), 낙상 확률 임계값과 무관.
    """

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    room: Mapped[str] = mapped_column(Text, nullable=False)  # HOME 은 '호' 접미어 없음
    connection: Mapped[str] = mapped_column(Text, nullable=False)
    mqtt_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial_port: Mapped[str | None] = mapped_column(Text, nullable=True)
    mac: Mapped[str | None] = mapped_column(Text, nullable=True)
    fw: Mapped[str | None] = mapped_column(Text, nullable=True)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    base_rssi: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_rssi: Mapped[float | None] = mapped_column(Float, nullable=True)
    agc: Mapped[float | None] = mapped_column(Float, nullable=True)
    noise_floor: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrating: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    calibration_stage: Mapped[str] = mapped_column(
        Text, nullable=False, default="IDLE", server_default=text("'IDLE'")
    )
    calibration_progress: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    presence_mv_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    wander_baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibration_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        scope_xor(),
        CheckConstraint("connection IN ('MQTT','SERIAL','SPI')", name="connection"),
        CheckConstraint(
            "calibration_stage IN ('IDLE','LEAVING','WAITING_ACK','WAITING_AGC','MEASURING','DONE','ERROR')",
            name="calibration_stage",
        ),
        CheckConstraint(
            "calibration_progress >= 0 AND calibration_progress <= 1", name="calibration_progress_range"
        ),
        CheckConstraint(
            "(connection = 'MQTT' AND mqtt_topic IS NOT NULL) OR (connection = 'SERIAL' AND serial_port IS NOT NULL) OR connection = 'SPI'",
            name="connection_target",
        ),
        Index(
            "ux_devices_scope_topic",
            text("COALESCE(facility_id, owner_user_id)"),
            "mqtt_topic",
            unique=True,
            postgresql_where=text("mqtt_topic IS NOT NULL"),
        ),
    )
