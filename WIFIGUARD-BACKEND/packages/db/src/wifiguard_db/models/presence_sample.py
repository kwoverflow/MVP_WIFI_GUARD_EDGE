"""재실 지표 시계열 — deploy/aws/init-timescale.sql 과 동일 컬럼의 일반 테이블 (Core Table, ORM 매핑 없음).

PK 가 없고 FK 도 없다(엣지가 올리는 고빈도 데이터라 참조 무결성 검사를 걸지 않는다).
TimescaleDB 확장이 있는 환경에서는 init-timescale.sql 처럼 `create_hypertable` 로 승격하면 된다 (D4).
이번 단계(CRUD·인증)에서는 기록하지 않는다.
필드명은 MQTT PresenceMsg(명세 backend §5.1)를 따른다 — WS /ws/live 의 presence_* 접두 이름과 다르다.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Table, Text

from ..base import Base

presence_samples = Table(
    "presence_samples",
    Base.metadata,
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("facility_id", Text, nullable=False),
    Column("device_id", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("mv_current", Float),
    Column("wander_current", Float),
    Column("mv_threshold", Float),
    Column("wander_baseline", Float),
    Column("wander_ratio_threshold", Float),
    Column("wander_ratio", Float),
    Column("wander_confirmed", Boolean),
    Column("last_activity_at", Float),
    Column("seconds_since_activity", Float),
    Column("just_changed", Boolean),
    Index("presence_samples_device_ts", "facility_id", "device_id", "ts"),
)
