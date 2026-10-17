"""Kafka 레코드 계약 (Pydantic v2) — 브리지 · 모델서버 · 인제스트의 SSOT.

토픽 3종은 `deploy/aws/user-data-kafka.sh` 가 만드는 것과 같아야 한다 (topics.py 상수).

    csi-feature-stream    FeatureRecord   presence(4Hz) + signal(게이트 개방 시 4Hz)
    csi-telemetry         StatusRecord    telemetry(~1Hz) + ack(이벤트)
    csi-inference-result  InferenceResult 모델서버 → 백엔드

**한 토픽에 두 종류가 섞이는 이유**: 엣지가 발행하는 순서를 보존해야 하기 때문이다.
같은 기기의 presence 와 signal 이 다른 토픽으로 가면 파티션이 갈라져 순서 보장이 사라지고,
"이 신호가 재실 상태 무엇일 때 잡힌 것인가"를 재구성할 수 없다. 파티션 키를 `device_id` 로
두면 기기별 전순서가 유지된다.

소비자는 `payload.kind` 로 걸러 쓴다:
    모델서버   csi-feature-stream 에서 kind == "signal" 만
    인제스트   csi-feature-stream 에서 kind == "presence" 만, csi-telemetry 전부

봉투를 씌우는 이유: MQTT 페이로드는 엣지가 만든 것이라 신뢰할 수 없다. 브리지가 검증한
사실(어느 토픽으로 실제로 들어왔는가, 언제 받았는가)을 엣지가 주장한 내용과 분리해 남긴다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from .mqtt import (
    AckMsg,
    PresenceMsg,
    SignalMsg,
    TelemetryMsg,
    WireModel,
)

SCHEMA_VERSION = 1


class _Receipt(WireModel):
    """브리지가 관측한 사실. 엣지가 주장한 값과 분리한다."""

    schema_version: Literal[1] = SCHEMA_VERSION
    received_at: datetime = Field(description="브리지가 MQTT 로 받은 시각 (클라우드 시계)")
    source_topic: str = Field(description="실제로 들어온 MQTT 토픽 원문")
    tenant_id: str = Field(description="토픽에서 파싱한 값. payload 의 주장과 대조 완료")
    device_id: UUID = Field(description="토픽에서 파싱한 값. payload 의 주장과 대조 완료")


class FeatureRecord(_Receipt):
    """`csi-feature-stream`. 파티션 키 = `str(device_id)`."""

    payload: Annotated[PresenceMsg | SignalMsg, Field(discriminator="kind")]


class StatusRecord(_Receipt):
    """`csi-telemetry`. 파티션 키 = `str(device_id)`."""

    payload: Annotated[TelemetryMsg | AckMsg, Field(discriminator="kind")]


# ── 추론 결과 (모델서버 → 백엔드) ─────────────────────────────────────
PostProcess = Literal[
    "none",
    "causal_mode5",
    "centered_mode5",
    "segmentation_a",
    "segmentation_b",
]
"""어느 후처리로 판정했는가. **반드시 데이터에 남긴다.**

검증 실측(`_reference/.../outputs/summary.json`): threshold 0.468 단독은 fall_f1 0.630 / 오탐 13,
centered mode5 를 얹으면 fall_f1 0.800 / 오탐 3 이다. 즉 0.468 이라는 값 자체가 mode5 를
전제로 고른 것이라, 어느 후처리를 썼는지 모르면 사후에 오탐률을 해석할 수 없다.
"""


class InferenceResult(WireModel):
    """`csi-inference-result` — 모델서버가 발행, 백엔드가 소비.

    **모델서버는 확률까지만 낸다.** 상태 판정(FallStateMachine)과 `fall_events` 생성은
    백엔드 서비스 영역이다 — "클라우드 백엔드 서버가 유저 상태관리·낙상 알림을 맡는다"는
    설계 의도에 맞춘 경계다. `state`/`fall_count` 를 여기 싣지 않는 이유가 그것이다.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    kind: Literal["inference"] = "inference"
    schema_version: Literal[1] = SCHEMA_VERSION

    tenant_id: str
    device_id: UUID
    ts: datetime = Field(description="원본 SignalMsg.ts — 윈도우 끝 시각 (엣지 시계)")
    seq: int = Field(ge=0, description="원본 SignalMsg.seq. 중복 추론 제거용")

    proba_fall: float = Field(ge=0, le=1, description="softmax(logits)[1]. class 1 = fall")
    threshold: float = Field(ge=0, le=1, description="판정에 쓰인 임계값 (기본 0.468)")
    postprocess: PostProcess
    decision: bool | None = Field(
        default=None,
        description="모델 패키지의 인과 후처리가 낸 최종 판정. none이면 백엔드가 후처리한다.",
    )

    # 관측용 — 이 셋이 없으면 지연 문제를 사후에 진단할 수 없다
    inferred_at: datetime = Field(description="모델서버가 추론을 끝낸 시각")
    feature_ms: float | None = Field(default=None, ge=0, description="signal → S3+ACF 변환 시간")
    infer_ms: float | None = Field(default=None, ge=0, description="텐서 → 확률 시간")
    model_version: str | None = Field(default=None, description="체크포인트 식별자 (epoch·해시)")
    scale_cache_hit: bool | None = Field(
        default=None,
        description="freq_to_scale 캐시 적중 여부. 미적중은 호출당 약 0.5초라 즉시 예산 초과다"
        " (common.py:199-204). 적중률이 떨어지면 _SCALE_CACHE_MAX=64 를 올려야 한다",
    )


__all__ = [
    "FeatureRecord",
    "InferenceResult",
    "PostProcess",
    "SCHEMA_VERSION",
    "StatusRecord",
]
