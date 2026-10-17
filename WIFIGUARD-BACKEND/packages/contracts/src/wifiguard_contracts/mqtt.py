"""엣지 ↔ 클라우드 MQTT 메시지 계약 (Pydantic v2) — 4개 레포의 SSOT.

**필드명은 snake_case 다.** `api.py` 가 camelCase 인 것과 의도적으로 다르다 — 저쪽은 프론트가
소비하는 REST 계약이고, 이쪽은 엣지 파이썬 dataclass 와 DB 컬럼 사이의 계약이기 때문이다.

이름 규칙의 근거 (PresenceMsg):
    `wifiguard_edge.presence.state_machine.PresenceStatus` 의 11필드와
    `wifiguard_db.models.presence_sample.presence_samples` 의 11개 데이터 컬럼이
    **이미 문자 단위로 일치**한다. 어긋나는 곳은 중간의 `presence_loop._payload()` 하나뿐이고,
    그 함수가 `seconds_since_activity` 를 버리고 3개를 `presence_*` 로 개명하고 있었다.
    양쪽 끝이 일치하므로 중간의 리네임을 없애는 것이 최소 변경이다.

    구 WS 계약이 `presence_*` 접두를 쓴 이유는 페이로드가 **평탄한 dict** 여서 재실의
    `mv_threshold` 와 낙상의 `threshold` 가 같은 평면에서 충돌했기 때문이다. 새 WS 계약
    (`realtime.py`)은 `{link, presence, fall}` 중첩이라 충돌이 구조적으로 불가능하고,
    따라서 접두가 필요 없다.

`WindowMsg` 는 존재하지 않는다 — topics.py 상단 주석 참조.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:  # numpy 는 신호를 실제로 인코드/디코드하는 쪽(엣지·모델서버)에만 필요하다.
    import numpy as np  # pragma: no cover

# ── 공통 별칭 ────────────────────────────────────────────────────────
PresenceState = Literal["present", "absent"]
"""소문자다. `PresenceState` Enum 의 `.value` 와 일치한다 (state_machine.py:17-18).
REST 쪽 `api.Presence` 는 대문자 PRESENT/ABSENT 라 서로 다르다 — 변환 지점은 백엔드 어댑터 한 곳."""

TransportKind = Literal["uart", "spi", "replay"]
SignalEncoding = Literal["f32le_b64"]
"""확장 예약: "f32le_zstd_b64". 지금 압축하지 않는 근거는 SignalMsg docstring 참조."""

SCHEMA_VERSION = 1


class WireModel(BaseModel):
    """엣지↔클라우드 와이어 모델의 기반.

    - snake_case 유지 (alias_generator 없음)
    - `extra="forbid"`: 엣지가 오타 필드를 보내면 조용히 무시되지 않고 즉시 실패한다.
      고빈도 스트림에서 조용한 필드 유실은 며칠 뒤 "왜 컬럼이 전부 NULL이지"로 돌아온다.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class _Envelope(WireModel):
    """모든 업링크 메시지가 공유하는 봉투."""

    schema_version: Literal[1] = SCHEMA_VERSION
    device_id: UUID
    tenant_id: str = Field(min_length=1, description="시설 UUID 문자열 또는 home-{userId}")
    ts: datetime = Field(description="엣지 wall clock, UTC aware")
    seq: int = Field(ge=0, description="기기 부팅 후 단조 증가. 중복 제거·유실 탐지용")


# ── presence ────────────────────────────────────────────────────────
class PresenceMsg(_Envelope):
    """`wifiguard/{tenant}/{device}/presence` — 4Hz.

    아래 11필드는 `PresenceStatus`(state_machine.py:25-35) 와 이름·순서가 같다.
    `presence_samples` 테이블에도 같은 이름으로 그대로 들어간다.
    """

    kind: Literal["presence"] = "presence"
    tick_uptime_s: float = Field(ge=0, description="루프 monotonic 가동 시간. wall clock 점프 진단용")

    state: PresenceState
    mv_current: float | None = None
    wander_current: float | None = None
    mv_threshold: float | None = None
    wander_baseline: float | None = None
    wander_ratio_threshold: float | None = None
    wander_ratio: float | None = None
    wander_confirmed: bool | None = None
    last_activity_at: float | None = Field(default=None, description="unix epoch seconds (DB Float 컬럼)")
    seconds_since_activity: float | None = None
    just_changed: bool | None = None


#: `presence_samples` 에 그대로 INSERT 되는 컬럼들. 파리티 테스트가 이 집합을 검증한다.
PRESENCE_STATUS_FIELDS: tuple[str, ...] = (
    "state",
    "mv_current",
    "wander_current",
    "mv_threshold",
    "wander_baseline",
    "wander_ratio_threshold",
    "wander_ratio",
    "wander_confirmed",
    "last_activity_at",
    "seconds_since_activity",
    "just_changed",
)


# ── signal (D1의 핵심) ───────────────────────────────────────────────
class SignalMsg(_Envelope):
    """`wifiguard/{tenant}/{device}/signal` — 게이트 개방 시 4Hz.

    `signal_b64` 는 기존 파이프라인과 진단 호환성을 위해 유지한다. `amplitude_b64` 는
    ACF-derivative 모델이 요구하는 균일 리샘플된 ``(T, 30)`` 진폭 창이다. 이 모델의
    서브캐리어 평균 lag-product는 1-D PCA 신호에서 복원할 수 없으므로, 새 추론 경로는
    amplitude 세 필드가 모두 있는 메시지만 처리한다.

    인코딩이 float32 raw base64 인 근거 — **양자화하면 안 된다**:
        이 신호는 클라우드에서 `q_metric(signal, 0.4*fs)`(common.py:266)을 거치고,
        그 q 가 `general_denoise` 의 임계값으로 직접 들어간다. q 는
        max(이동분산)/mean(이동분산) 인데, 양자화 노이즈는 정적 구간의 **분산 바닥을
        균일하게 들어올려** 분모를 키우고 q 를 낮춘다. 즉 int8 양자화는 S3 디노이즈 강도를
        체계적으로 바꾼다 — common.py:4-5 가 "원본과 달라지면 모델 성능이 무효"라고
        경고한 바로 그 위험을 새로 주입하는 셈이다.

    압축(zstd)하지 않는 근거:
        절약분이 약 800B/윈도우 = 25kbps/기기다. 10기기여도 250kbps 로 무의미한 반면,
        Pi·클라우드 양쪽에 의존이 늘고 `kafka-console-consumer` 로 눈으로 볼 수 없게 된다.
        정당화되는 임계는 대략 기기 100대이고, 그때는 `encoding` 값만 늘리면 된다.

    amplitude 크기: fs=320, T=960, C=30 → raw 115,200B → b64 153,600B
    → 4Hz 시 약 4.9Mbps/기기. MQTT/Kafka 기본 단일 메시지 제한에는 들어가지만,
    다기기 운영 전 압축 또는 엣지 피처화 검토가 필요하다.
    """

    kind: Literal["signal"] = "signal"

    encoding: SignalEncoding = "f32le_b64"
    signal_b64: str = Field(min_length=1)
    signal_len: int = Field(gt=0, description="디코드 후 샘플 수. 위조·절단 검증용")
    amplitude_b64: str | None = Field(
        default=None,
        description="선택·리샘플된 float32 (rows, cols) 진폭 창. temporal segmentation 모델 입력",
    )
    amplitude_rows: int | None = Field(default=None, gt=0)
    amplitude_cols: int | None = Field(default=None, gt=0)
    fs_hz: float = Field(gt=0, description="0.25Hz 격자로 양자화된 값 (realtime.py:117-118)")
    window_samples: int = Field(gt=0)
    window_span_s: float = Field(gt=0, description="실제 관측 span = times[-1]-times[0]")

    # select_subcarrier_indices / select_streams / select_pc_signal 산출
    selected_subcarrier_count: int = Field(gt=0)
    selected_stream_count: int = Field(gt=0)
    selected_pc_indices: str = Field(description='common.py:90 형식 그대로. 예: "0;2"')
    candidate_pc_count: int = Field(gt=0)
    selected_pc_count: int = Field(gt=0)
    input_frames: int = Field(gt=0)
    input_subcarriers: int = Field(gt=0)
    signal_q: float | None = Field(default=None, description="엣지가 계산한 q. 클라우드 재계산값과 대조용")

    # 왜 이 윈도우를 올렸는가 — 게이트가 닫혀 있었다는 사실도 관측 가능해야 한다
    presence_state: PresenceState
    gate_reason: Literal["present", "forced", "calibration"]

    def decode(self) -> "np.ndarray":
        """`signal_b64` → `(signal_len,) float32`. 길이가 어긋나면 ValueError. numpy 필요."""
        return decode_signal(self.signal_b64, self.signal_len, self.encoding)

    def decode_amplitude(self) -> "np.ndarray":
        """``amplitude_b64`` → ``(rows, cols) float32``. 누락·절단은 ValueError."""
        if self.amplitude_b64 is None or self.amplitude_rows is None or self.amplitude_cols is None:
            raise ValueError(
                "temporal segmentation 추론에는 "
                "amplitude_b64/amplitude_rows/amplitude_cols가 필요하다"
            )
        return decode_amplitude(
            self.amplitude_b64,
            self.amplitude_rows,
            self.amplitude_cols,
            self.encoding,
        )

    @model_validator(mode="after")
    def _amplitude_fields_are_all_or_none(self) -> "SignalMsg":
        supplied = (
            self.amplitude_b64 is not None,
            self.amplitude_rows is not None,
            self.amplitude_cols is not None,
        )
        if any(supplied) and not all(supplied):
            raise ValueError(
                "amplitude_b64, amplitude_rows, amplitude_cols는 모두 함께 제공해야 한다"
            )
        return self


def encode_signal(signal: "np.ndarray", encoding: SignalEncoding = "f32le_b64") -> str:
    """`select_pc_signal()` 반환값을 와이어 문자열로. 엣지와 클라우드가 같은 함수를 쓴다."""
    import numpy as np

    if encoding != "f32le_b64":
        raise ValueError(f"지원하지 않는 인코딩: {encoding}")
    arr = np.ascontiguousarray(signal, dtype="<f4")
    if arr.ndim != 1:
        raise ValueError(f"1-D 신호여야 한다: shape={arr.shape}")
    return base64.b64encode(arr.tobytes()).decode("ascii")


def decode_signal(signal_b64: str, signal_len: int, encoding: SignalEncoding = "f32le_b64") -> "np.ndarray":
    import numpy as np

    if encoding != "f32le_b64":
        raise ValueError(f"지원하지 않는 인코딩: {encoding}")
    arr = np.frombuffer(base64.b64decode(signal_b64), dtype="<f4")
    if arr.size != signal_len:
        raise ValueError(f"신호 길이 불일치: 디코드 {arr.size} != 선언 {signal_len}")
    return np.array(arr, dtype=np.float32)  # 쓰기 가능한 사본 (frombuffer 는 읽기 전용)


def encode_amplitude(amplitude: "np.ndarray", encoding: SignalEncoding = "f32le_b64") -> str:
    """``(time, 30)`` 진폭 창을 float32 little-endian base64로 무손실 인코딩한다."""
    import numpy as np

    if encoding != "f32le_b64":
        raise ValueError(f"지원하지 않는 인코딩: {encoding}")
    arr = np.ascontiguousarray(amplitude, dtype="<f4")
    if arr.ndim != 2:
        raise ValueError(f"2-D 진폭 창이어야 한다: shape={arr.shape}")
    return base64.b64encode(arr.tobytes()).decode("ascii")


def decode_amplitude(
    amplitude_b64: str,
    rows: int,
    cols: int,
    encoding: SignalEncoding = "f32le_b64",
) -> "np.ndarray":
    """무손실 base64 진폭 창을 ``(rows, cols) float32``로 복원한다."""
    import numpy as np

    if encoding != "f32le_b64":
        raise ValueError(f"지원하지 않는 인코딩: {encoding}")
    arr = np.frombuffer(base64.b64decode(amplitude_b64), dtype="<f4")
    expected = rows * cols
    if arr.size != expected:
        raise ValueError(f"진폭 길이 불일치: 디코드 {arr.size} != 선언 {rows}x{cols}")
    return np.array(arr.reshape(rows, cols), dtype=np.float32)


# ── telemetry ───────────────────────────────────────────────────────
class LinkStats(WireModel):
    """수신기 링크 상태. 구 `/monitor/status` 의 시리얼 그룹과 `/ws/live` 기본 필드를 계승한다."""

    connected: bool
    transport: TransportKind
    port: str | None = None
    baud: int | None = None
    reconnects: int = 0
    frames_ok: int = 0
    checksum_errors: int = 0
    resyncs: int = 0
    mac_filtered: int = 0
    hz_1s: float | None = None
    rssi: float | None = None
    buffered_seconds: float | None = None
    amp_mean: float | None = None
    amp_std: float | None = None


class LoopStats(WireModel):
    """PresenceLoop / FeatureLoop 공통 헬스. `presence_loop.status()` 의 4필드."""

    enabled: bool
    tick_count: int = 0
    skip_count: int = 0
    last_error: str | None = None


class TelemetryMsg(_Envelope):
    """`wifiguard/{tenant}/{device}/telemetry` — 약 1Hz.

    **게이트가 닫혀 있었다는 사실 자체를 항상 보고해야 한다** (`config/default.toml [gating]` 주석).
    무증상 침묵과 "정상적으로 조용함"을 구별할 수 있어야 하기 때문이다.
    """

    kind: Literal["telemetry"] = "telemetry"

    link: LinkStats
    presence_loop: LoopStats
    feature_loop: LoopStats
    gate_open: bool = Field(description="지금 signal 을 발행하고 있는가")
    signals_published: int = Field(default=0, ge=0)
    signals_gated: int = Field(default=0, ge=0, description="게이트가 막은 윈도우 수")
    fw_version: str | None = None
    edge_version: str | None = None
    uptime_s: float | None = None


# ── cmd / ack ───────────────────────────────────────────────────────
CmdAction = Literal["calibrate", "set_config", "set_mode", "ping"]


class CmdMsg(WireModel):
    """`wifiguard/{tenant}/{device}/cmd` — 클라우드 → 엣지. 이벤트성.

    `_Envelope` 를 쓰지 않는다 — 하향 메시지는 `seq` 대신 `cmd_id` 로 ack 를 짝짓는다.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["cmd"] = "cmd"
    schema_version: Literal[1] = SCHEMA_VERSION
    device_id: UUID
    tenant_id: str = Field(min_length=1)
    ts: datetime
    cmd_id: UUID = Field(description="ack 가 이 값을 되돌려준다")
    action: CmdAction
    args: dict[str, Any] = Field(default_factory=dict)


AckStatus = Literal["accepted", "progress", "done", "error"]


class AckMsg(_Envelope):
    """`wifiguard/{tenant}/{device}/ack` — 엣지 → 클라우드.

    캘리브레이션은 61.2초 4단계라 하나의 cmd 에 대해 `progress` ack 가 여러 번 온 뒤
    `done` 으로 끝난다. 그래서 status 가 4값이다.
    """

    kind: Literal["ack"] = "ack"
    cmd_id: UUID
    action: CmdAction
    status: AckStatus
    stage: str | None = Field(default=None, description="캘리브레이션 단계 (api.CalibrationStage 와 동일 값)")
    progress: float | None = Field(default=None, ge=0, le=1)
    detail: str | None = None
    result: dict[str, Any] = Field(default_factory=dict, description="캘리브레이션 산출 임계값 등")


UplinkMsg = Annotated[
    PresenceMsg | SignalMsg | TelemetryMsg | AckMsg,
    Field(discriminator="kind"),
]

__all__ = [
    "AckMsg",
    "AckStatus",
    "CmdAction",
    "CmdMsg",
    "LinkStats",
    "LoopStats",
    "PRESENCE_STATUS_FIELDS",
    "PresenceMsg",
    "PresenceState",
    "SCHEMA_VERSION",
    "SignalEncoding",
    "SignalMsg",
    "TelemetryMsg",
    "TransportKind",
    "UplinkMsg",
    "WireModel",
    "decode_amplitude",
    "decode_signal",
    "encode_amplitude",
    "encode_signal",
]
