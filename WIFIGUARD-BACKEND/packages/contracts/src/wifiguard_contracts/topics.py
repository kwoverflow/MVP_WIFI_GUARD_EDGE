"""MQTT 토픽 · Kafka 토픽 이름의 단일 진실원.

토픽 문자열을 손으로 조립하는 곳이 두 군데 이상 생기면 즉시 갈라진다. 실제로 그럴 뻔했다 —
`services/api/.../routers/devices.py` 의 `issue_mqtt_topic()` 과 엣지의
`config/default.toml [mqtt] topic_prefix` 가 같은 규칙을 따로 알고 있었다. 여기로 모은다.

MQTT 토픽 구조 (명세 backend §7.1)::

    wifiguard/{tenant}/{device}/{leaf}

- ``tenant`` : FACILITY 는 시설 UUID 문자열, HOME 은 ``home-{userId}``
- ``device`` : 기기 UUID 문자열
- ``leaf``   : presence | telemetry | signal | cmd | ack

``window`` leaf 는 존재하지 않는다. ACF-derivative 모델에 필요한 선택된 30채널
진폭 창은 1-D 호환 신호와 함께 ``signal`` 페이로드에 싣는다. 실제 모델 입력은
``SignalMsg.amplitude_*``이며, 1-D ``signal_b64``는 진단과 구 계약 호환을 위해 유지한다.
"""

from __future__ import annotations

import re
from typing import Final, Literal, NamedTuple
from uuid import UUID

PREFIX: Final = "wifiguard"

Leaf = Literal["presence", "telemetry", "signal", "cmd", "ack"]

#: 엣지 → 클라우드 (기기가 발행)
UPLINK_LEAVES: Final[tuple[Leaf, ...]] = ("presence", "telemetry", "signal", "ack")
#: 클라우드 → 엣지 (기기가 구독)
DOWNLINK_LEAVES: Final[tuple[Leaf, ...]] = ("cmd",)

# ── Kafka 토픽 (deploy/aws/user-data-kafka.sh 가 생성하는 3종과 일치해야 한다) ──
KAFKA_FEATURE_STREAM: Final = "csi-feature-stream"
KAFKA_TELEMETRY: Final = "csi-telemetry"
KAFKA_INFERENCE_RESULT: Final = "csi-inference-result"

KAFKA_TOPICS: Final[tuple[str, ...]] = (KAFKA_FEATURE_STREAM, KAFKA_TELEMETRY, KAFKA_INFERENCE_RESULT)

# tenant 는 UUID 문자열 또는 home-{uuid}. MQTT 와일드카드/구분자를 절대 포함할 수 없다.
_TENANT_RE: Final = re.compile(r"^(?:home-)?[0-9a-fA-F-]{36}$")


class TopicParts(NamedTuple):
    """파싱된 토픽. `leaf` 가 None 이면 leaf 없는 base 토픽이다."""

    tenant: str
    device_id: str
    leaf: Leaf | None


def tenant_id(*, facility_id: UUID | str | None, user_id: UUID | str | None) -> str:
    """스코프에서 tenant 문자열을 만든다. FACILITY 우선, 없으면 HOME.

    `routers/devices.py:issue_mqtt_topic()` 과 `presence_samples.facility_id` 컬럼이 같은 값을 쓴다.
    컬럼 타입이 UUID 가 아니라 TEXT 인 이유가 `home-` 접두 때문이다.
    """
    if facility_id is not None:
        return str(facility_id)
    if user_id is None:
        raise ValueError("facility_id 와 user_id 가 모두 없다 — 스코프 XOR 위반")
    return f"home-{user_id}"


def device_base(tenant: str, device_id: UUID | str) -> str:
    """`wifiguard/{tenant}/{device}` — leaf 없는 기기 base 토픽.

    DB `devices.mqtt_topic` 에 저장되는 값이 이 형식이다.
    """
    _check_segment(tenant, "tenant")
    _check_segment(str(device_id), "device_id")
    return f"{PREFIX}/{tenant}/{device_id}"


def leaf_topic(tenant: str, device_id: UUID | str, leaf: Leaf) -> str:
    """`wifiguard/{tenant}/{device}/{leaf}`."""
    return f"{device_base(tenant, device_id)}/{leaf}"


def subscribe_all_uplink() -> str:
    """브리지가 모든 기기의 업링크를 받는 구독 패턴."""
    return f"{PREFIX}/+/+/+"


def parse(topic: str) -> TopicParts:
    """토픽을 파싱한다. 형식이 어긋나면 ValueError.

    브리지는 **페이로드의 device_id 가 토픽의 device_id 와 일치하는지 반드시 대조**해야 한다.
    그러지 않으면 한 기기가 다른 테넌트의 토픽으로 발행해 남의 데이터를 위조할 수 있다.
    """
    parts = topic.split("/")
    if len(parts) not in (3, 4) or parts[0] != PREFIX:
        raise ValueError(f"토픽 형식이 아니다: {topic!r}")
    tenant, device_id = parts[1], parts[2]
    _check_segment(tenant, "tenant")
    _check_segment(device_id, "device_id")
    if not _TENANT_RE.match(tenant):
        raise ValueError(f"tenant 형식이 아니다: {tenant!r}")
    leaf = parts[3] if len(parts) == 4 else None
    if leaf is not None and leaf not in (*UPLINK_LEAVES, *DOWNLINK_LEAVES):
        raise ValueError(f"알 수 없는 leaf: {leaf!r}")
    return TopicParts(tenant=tenant, device_id=device_id, leaf=leaf)  # type: ignore[arg-type]


def _check_segment(value: str, what: str) -> None:
    if not value:
        raise ValueError(f"{what} 가 비어 있다")
    for bad in ("/", "+", "#"):
        if bad in value:
            raise ValueError(f"{what} 에 MQTT 예약문자 {bad!r} 가 있다: {value!r}")
