"""REST 요청/응답 스키마 (Pydantic v2) — OpenAPI 의 단일 진실원.

JSON 필드는 camelCase (alias_generator). 프론트 기존 이름(facilityId, deviceIds, mqttTopic …)을 유지한다.
Resident 의 런타임 필드(state, mv, …)는 이번 단계에서 항상 null 이다 — "감지 미동작" (D2).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

Service = Literal["HOME", "FACILITY"]
Role = Literal["ROOT", "MEMBER", "USER"]
FacilityMode = Literal["ROOT", "MEMBER"]
Connection = Literal["MQTT", "SERIAL", "SPI"]
CalibrationStage = Literal["IDLE", "LEAVING", "WAITING_ACK", "WAITING_AGC", "MEASURING", "DONE", "ERROR"]
FallResponse = Literal["PENDING", "ACKNOWLEDGED", "DISPATCHED", "FALSE_ALARM"]
FallSource = Literal["EDGE", "SIMULATED", "SEED"]
RecipientRole = Literal["FAMILY", "CAREGIVER", "ADMIN"]
LogLevel = Literal["INFO", "WARN", "ERROR", "FALL"]
ClientLogLevel = Literal["INFO", "WARN", "ERROR"]
FallState = Literal["IDLE", "SUSPECT", "FALL", "COOLDOWN"]
Presence = Literal["PRESENT", "ABSENT"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class ErrorOut(ApiModel):
    detail: str
    code: str
    fields: dict[str, Any] | None = None


# ── 계정 · 시설 ─────────────────────────────────────────────────────
class UserOut(ApiModel):
    id: UUID
    email: str
    name: str
    service: Service
    role: Role
    facility_id: UUID | None = None
    onboarded: bool
    created_at: datetime


class FacilityOut(ApiModel):
    id: UUID
    name: str
    invite_code: str
    root_user_id: UUID
    created_at: datetime


class MemberOut(ApiModel):
    id: UUID
    email: str
    name: str
    role: Role
    onboarded: bool
    created_at: datetime


class FacilityPatch(ApiModel):
    name: str = Field(min_length=1, max_length=100)


class Features(ApiModel):
    fall_simulate: bool


class MeOut(UserOut):
    facility: FacilityOut | None = None
    features: Features


# ── 인증 ────────────────────────────────────────────────────────────
class SignupIn(ApiModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    service: Service
    facility_mode: FacilityMode | None = None
    facility_name: str | None = Field(default=None, max_length=100)
    invite_code: str | None = Field(default=None, max_length=16)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("이메일 형식이 올바르지 않습니다.")
        return v


class LoginIn(ApiModel):
    email: str
    password: str


class RefreshIn(ApiModel):
    refresh_token: str


class LogoutIn(ApiModel):
    refresh_token: str | None = None


class TokenPair(ApiModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    user: UserOut


class AccountPatch(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("이메일 형식이 올바르지 않습니다.")
        return v


class PasswordChangeIn(ApiModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


# ── 기기 ────────────────────────────────────────────────────────────
class DeviceOut(ApiModel):
    id: UUID
    name: str
    room: str
    connection: Connection
    mqtt_topic: str | None = None
    serial_port: str | None = None
    mac: str | None = None
    fw: str | None = None
    online: bool | None = None
    """None = **모름**(텔레메트리를 한 번도 받은 적 없음), False = 연결 끊김.

    구 계약은 필수 bool 이라 "모름"을 표현할 수 없었고, 그래서 등록만 하고 아직 연결된 적
    없는 기기가 화면에 "연결 끊김"으로 **단정** 표시됐다. `ResidentOut` 의 런타임 필드는
    이미 nullable 이라 "감지 미동작"으로 올바르게 구분되는데, 기기 축만 그러지 못했다.

    DB 컬럼(`devices.online`)은 NOT NULL 그대로다 — 마이그레이션이 필요 없고, 응답에서만
    "텔레메트리를 받은 적 있는가"를 반영해 None 으로 낮춘다.
    """
    last_seen_at: datetime | None = None
    base_rssi: float | None = None
    current_rssi: float | None = None
    agc: float | None = None
    noise_floor: float | None = None
    calibrating: bool
    calibration_stage: CalibrationStage
    calibration_progress: float
    presence_mv_threshold: float | None = None
    wander_baseline: float | None = None
    facility_id: UUID | None = None
    owner_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class DeviceIn(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    room: str = Field(min_length=1, max_length=100)
    connection: Connection
    mqtt_topic: str | None = Field(default=None, max_length=200)  # 없으면 서버 발급 wifiguard/{scope}/{id}
    serial_port: str | None = Field(default=None, max_length=100)
    mac: str | None = Field(default=None, max_length=32)
    fw: str | None = Field(default=None, max_length=32)


class DevicePatch(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    room: str | None = Field(default=None, min_length=1, max_length=100)
    mqtt_topic: str | None = Field(default=None, max_length=200)
    serial_port: str | None = Field(default=None, max_length=100)
    mac: str | None = Field(default=None, max_length=32)
    fw: str | None = Field(default=None, max_length=32)
    online: bool | None = None
    calibrating: bool | None = None
    calibration_stage: CalibrationStage | None = None
    calibration_progress: float | None = Field(default=None, ge=0, le=1)
    presence_mv_threshold: float | None = Field(default=None, gt=0)
    wander_baseline: float | None = Field(default=None, ge=0)
    base_rssi: float | None = None
    current_rssi: float | None = None
    agc: float | None = None
    noise_floor: float | None = None


# ── 거주자 ──────────────────────────────────────────────────────────
class ResidentOut(ApiModel):
    id: UUID
    name: str
    room: str
    age: int | None = None
    caregiver: str | None = None
    device_id: UUID | None = None  # 주 장치
    device_ids: list[UUID] = Field(default_factory=list)  # 다중 매핑 (device_id 포함)
    threshold_override: float | None = None
    facility_id: UUID | None = None
    owner_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    # 런타임 — 실시간 경로 미구현 동안 항상 null ("감지 미동작")
    state: FallState | None = None
    mv: float | None = None
    wander: float | None = None
    presence: Presence | None = None
    last_activity_at: datetime | None = None
    confidence: float | None = None
    online: bool | None = None


class ResidentIn(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    room: str = Field(min_length=1, max_length=100)
    age: int | None = Field(default=None, ge=0, le=150)
    caregiver: str | None = Field(default=None, max_length=100)
    device_id: UUID | None = None
    device_ids: list[UUID] | None = None
    threshold_override: float | None = Field(default=None, gt=0)


class ResidentPatch(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    room: str | None = Field(default=None, min_length=1, max_length=100)
    age: int | None = Field(default=None, ge=0, le=150)
    caregiver: str | None = Field(default=None, max_length=100)
    device_id: UUID | None = None
    device_ids: list[UUID] | None = None
    threshold_override: float | None = Field(default=None, gt=0)
    clear_threshold_override: bool = False


# ── 낙상 이력 ───────────────────────────────────────────────────────
class FallOut(ApiModel):
    id: UUID
    resident_id: UUID | None = None
    resident_name: str
    room: str
    device_id: UUID | None = None
    occurred_at: datetime
    confidence: float
    duration_s: float
    source: FallSource
    response: FallResponse
    responded_by: UUID | None = None
    responded_at: datetime | None = None
    facility_id: UUID | None = None
    owner_user_id: UUID | None = None
    created_at: datetime


class FallResponseIn(ApiModel):
    response: FallResponse


class FallSimulateIn(ApiModel):
    resident_id: UUID | None = None


class Page(ApiModel):
    total: int
    limit: int
    offset: int


class FallPage(Page):
    items: list[FallOut]


# ── 이벤트 로그 ─────────────────────────────────────────────────────
class EventLogOut(ApiModel):
    id: int
    ts: datetime
    level: LogLevel
    msg: str
    resident_id: UUID | None = None
    device_id: UUID | None = None
    fall_event_id: UUID | None = None
    actor_user_id: UUID | None = None


class EventLogIn(ApiModel):
    level: ClientLogLevel
    msg: str = Field(min_length=1, max_length=500)
    resident_id: UUID | None = None
    device_id: UUID | None = None


class EventLogPage(Page):
    items: list[EventLogOut]


# ── 수신자 ──────────────────────────────────────────────────────────
class RecipientOut(ApiModel):
    id: UUID
    name: str
    role: RecipientRole
    phone: str | None = None
    email: str | None = None
    email_enabled: bool
    sms: bool
    push: bool
    ars: bool
    ntfy_server: str | None = None
    ntfy_topic: str | None = None
    enabled: bool
    resident_id: UUID | None = None
    facility_id: UUID | None = None
    owner_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class RecipientIn(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    role: RecipientRole
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    email_enabled: bool = False
    sms: bool = False
    push: bool = False
    ars: bool = False
    ntfy_server: str | None = Field(default=None, max_length=200)
    ntfy_topic: str | None = Field(default=None, max_length=200)
    enabled: bool = True
    resident_id: UUID | None = None


class RecipientPatch(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: RecipientRole | None = None
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    email_enabled: bool | None = None
    sms: bool | None = None
    push: bool | None = None
    ars: bool | None = None
    ntfy_server: str | None = Field(default=None, max_length=200)
    ntfy_topic: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    resident_id: UUID | None = None
    clear_resident: bool = False


# ── 테넌트 설정 (세 축의 임계값) ────────────────────────────────────
class TenantConfigOut(ApiModel):
    presence_mv_threshold: float
    wander_ratio_threshold: float
    presence_timeout_s: float
    threshold: float
    cooldown_seconds: float
    updated_at: datetime
    updated_by: UUID | None = None


class TenantConfigIn(ApiModel):
    presence_mv_threshold: float = Field(gt=0, le=10)
    wander_ratio_threshold: float = Field(ge=1.0, le=5.0)
    presence_timeout_s: float = Field(gt=0, le=60)
    threshold: float = Field(ge=0, le=1)
    cooldown_seconds: float = Field(ge=0, le=60)
