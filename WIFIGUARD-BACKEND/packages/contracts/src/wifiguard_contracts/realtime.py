"""`/ws/live` 실시간 계약 (Pydantic v2) — 백엔드 ↔ 프론트.

OpenAPI 는 WebSocket 을 표현하지 못한다. 그래서 이 모듈의 JSON Schema 를
`GET /realtime/schema` 로 내보내고 프론트가 거기서 타입을 생성한다 — 지금처럼
`src/lib/backend.ts:87-112` 에서 손으로 미러링하면 서버 계약과 조용히 갈라진다.

## 구 계약과 달라진 점: 평탄한 dict → 중첩

구 `main.py:336-352` 는 세 출처를 **한 평면에 병합**했다::

    payload.update(presence_loop.live_payload())   # mv_threshold …
    payload.update(detector.live_payload())        # threshold …

재실의 `mv_threshold` 와 낙상의 `threshold` 가 같은 평면에서 충돌하니 재실 쪽에
`presence_` 접두를 붙일 수밖에 없었다. 중첩으로 바꾸면 충돌이 구조적으로 불가능해지고,
그 덕에 `PresenceStatus` → MQTT → DB → WS 전 구간에서 **필드명이 한 번도 바뀌지 않는다**.

## 안전 요구 — 낙상 필드의 부재는 "낙상 없음"이 아니다

`fall` 이 None 이면 **낙상 감지가 동작하지 않는 것**이다. 낙상이 없다는 뜻이 아니다.
`presence` 도 마찬가지다. 프론트는 두 상태를 반드시 구분해 표시해야 한다
("감지 미동작" vs "재실/퇴실"). Optional 로 둔 것이 그 계약의 표현이다.

## 필드명이 snake_case 인 이유

`api.py`(REST)는 camelCase 지만 여기는 snake_case 다. 프론트의 기존 `LiveSample` 도
이미 snake_case 를 쓰고 있다(`presence_state`, `mv_current`, `proba_fall`) — 구 백엔드를
그대로 미러링했기 때문이다. 여기서 camelCase 로 바꾸면 핫패스에 리네임 계층이 새로 생기고,
`PresenceStatus`=DB 컬럼 SSOT 가 마지막 한 구간에서 깨진다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from .kafka import PostProcess
from .mqtt import LinkStats, PresenceState, WireModel

PROTOCOL_VERSION = 1

# ── 서버 → 클라이언트 ────────────────────────────────────────────────
class PresenceBlock(WireModel):
    """`PresenceMsg` 의 11필드 그대로. 이름이 바뀌지 않는다."""

    state: PresenceState
    mv_current: float | None = None
    wander_current: float | None = None
    mv_threshold: float | None = None
    wander_baseline: float | None = None
    wander_ratio_threshold: float | None = None
    wander_ratio: float | None = None
    wander_confirmed: bool | None = None
    last_activity_at: float | None = None
    seconds_since_activity: float | None = None
    just_changed: bool | None = None
    updated_at: datetime


class FallBlock(WireModel):
    """구 `detector.live_payload()`(state_machine.py:215-225) 5필드 + 후처리 표기.

    `postprocess` 를 실어 보내는 이유는 kafka.PostProcess docstring 참조 —
    같은 임계값이라도 후처리에 따라 오탐률이 4배 달라진다.
    """

    detect_state: Literal["IDLE", "SUSPECT", "FALL", "COOLDOWN"]
    proba_fall: float | None = None
    threshold: float
    postprocess: PostProcess
    fall_count: int = 0
    last_fall_time: float | None = None
    fall_event_id: UUID | None = Field(default=None, description="Committed fall_events row ID for acknowledgement; None when not persisted")
    updated_at: datetime


class DeviceLive(WireModel):
    """한 기기의 현재 상태. `presence`/`fall` 이 None 이면 그 축이 동작하지 않는 것이다."""

    device_id: UUID
    online: bool | None = Field(
        default=None,
        description="None 은 '모름'(텔레메트리를 한 번도 받은 적 없음)이다. False('연결 끊김')와 다르다",
    )
    last_seen_at: datetime | None = None
    link: LinkStats | None = None
    presence: PresenceBlock | None = None
    fall: FallBlock | None = None


class HelloEvent(WireModel):
    """인증 성공 직후 1회. 구독 가능한 기기 목록을 알려준다."""

    type: Literal["hello"] = "hello"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    tenant_id: str
    user_id: UUID
    device_ids: list[UUID] = Field(description="이 사용자 스코프의 전체 기기")
    server_time: datetime


class LiveEvent(WireModel):
    """구독한 기기의 상태 갱신. 10Hz 상한으로 합쳐 보낸다(엣지는 4Hz)."""

    type: Literal["live"] = "live"
    devices: list[DeviceLive]
    server_time: datetime


class ErrorEvent(WireModel):
    type: Literal["error"] = "error"
    code: str
    detail: str


class PongEvent(WireModel):
    type: Literal["pong"] = "pong"
    server_time: datetime


ServerEvent = Annotated[
    HelloEvent | LiveEvent | ErrorEvent | PongEvent,
    Field(discriminator="type"),
]


# ── 클라이언트 → 서버 ────────────────────────────────────────────────
class AuthFrame(WireModel):
    """연결 후 **첫 프레임이어야 한다.** 5초 안에 오지 않으면 서버가 1008 로 닫는다.

    쿼리스트링이나 헤더가 아니라 프레임으로 받는 이유: 브라우저 WebSocket API 는 커스텀
    헤더를 붙일 수 없고, URL 에 토큰을 넣으면 프록시·서버 접근 로그에 그대로 남는다.
    """

    type: Literal["auth"] = "auth"
    token: str = Field(min_length=1, description="REST 와 같은 access JWT")


class SubscribeFrame(WireModel):
    """빈 목록은 '스코프 내 전체'다. 스코프 밖 기기는 조용히 무시된다(존재 여부 노출 방지)."""

    type: Literal["subscribe"] = "subscribe"
    device_ids: list[UUID] = Field(default_factory=list)


class UnsubscribeFrame(WireModel):
    type: Literal["unsubscribe"] = "unsubscribe"
    device_ids: list[UUID] = Field(default_factory=list)


class PingFrame(WireModel):
    type: Literal["ping"] = "ping"


ClientFrame = Annotated[
    AuthFrame | SubscribeFrame | UnsubscribeFrame | PingFrame,
    Field(discriminator="type"),
]

# ── close 코드 ──────────────────────────────────────────────────────
WS_CLOSE_UNAUTHORIZED = 1008
"""인증 실패 또는 5초 내 auth 미도착. RFC 6455 의 policy violation."""

WS_CLOSE_TOKEN_EXPIRED = 4001
"""access 토큰 만료. 프론트는 refresh 후 재연결한다 (백오프 없이 즉시 1회)."""


__all__ = [
    "AuthFrame",
    "ClientFrame",
    "DeviceLive",
    "ErrorEvent",
    "FallBlock",
    "HelloEvent",
    "LiveEvent",
    "PROTOCOL_VERSION",
    "PingFrame",
    "PongEvent",
    "PresenceBlock",
    "ServerEvent",
    "SubscribeFrame",
    "UnsubscribeFrame",
    "WS_CLOSE_TOKEN_EXPIRED",
    "WS_CLOSE_UNAUTHORIZED",
]
