"""엣지 내부 객체 → `wifiguard_contracts` 메시지 → JSON 바이트.

**여기가 유일한 변환 지점이다.** 필드를 손으로 dict 에 담는 코드가 여러 곳에 생기면
계약과 갈라진다 — 실제로 `presence_loop._payload()` 가 그렇게 이름을 바꾸고 있었다.

`PresenceMsg` 의 상태 11필드는 `presence_loop._payload()` 가 이미 계약과 **같은 이름**으로
내보내므로 그대로 통과시킨다. 이 파일에 리네임 매핑이 없는 것이 정상이며, 매핑이 생기려
한다면 양 끝 중 하나가 잘못된 것이다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from wifiguard_contracts.mqtt import (
    AckMsg,
    AckStatus,
    CmdAction,
    LinkStats,
    LoopStats,
    PresenceMsg,
    SignalMsg,
    TelemetryMsg,
    encode_amplitude,
    encode_signal,
)

from ..features.realtime import WindowSignal
from ..gating import GateDecision


def _now() -> datetime:
    return datetime.now(UTC)


def build_presence(
    payload: dict[str, Any],
    *,
    device_id: UUID,
    tenant_id: str,
    seq: int,
    tick_uptime_s: float,
) -> PresenceMsg | None:
    """`PresenceLoop.presence_payload()` → `PresenceMsg`.

    `state` 가 None 이면 **발행하지 않는다**(None 반환). 아직 한 틱도 돌지 않은 "미상"은
    present 도 absent 도 아니며, absent 로 올리면 클라우드가 "퇴실"로 기록해 버린다.
    """
    if payload.get("state") is None:
        return None
    return PresenceMsg(
        device_id=device_id,
        tenant_id=tenant_id,
        ts=_now(),
        seq=seq,
        tick_uptime_s=tick_uptime_s,
        **payload,
    )


def build_signal(
    window: WindowSignal,
    decision: GateDecision,
    *,
    device_id: UUID,
    tenant_id: str,
    seq: int,
    presence_state: str | None,
) -> SignalMsg:
    """`WindowSignal` → `SignalMsg`. float32 무손실 base64 (양자화하지 않는다)."""
    stats = window.stats
    return SignalMsg(
        device_id=device_id,
        tenant_id=tenant_id,
        ts=_now(),
        seq=seq,
        signal_b64=encode_signal(window.signal),
        signal_len=int(len(window.signal)),
        amplitude_b64=encode_amplitude(window.amplitude),
        amplitude_rows=int(window.amplitude.shape[0]),
        amplitude_cols=int(window.amplitude.shape[1]),
        fs_hz=window.fs_hz,
        window_samples=window.window_samples,
        window_span_s=window.window_span_s,
        selected_subcarrier_count=int(stats["selected_subcarrier_count"]),
        selected_stream_count=int(stats["selected_stream_count"]),
        selected_pc_indices=str(stats["selected_pc_indices"]),
        candidate_pc_count=int(stats["candidate_pc_count"]),
        selected_pc_count=int(stats["selected_pc_count"]),
        input_frames=int(stats["input_frames"]),
        input_subcarriers=int(stats["input_subcarriers"]),
        signal_q=stats.get("signal_q"),
        # 게이트가 열렸다는 것은 재실이 present 이거나 유예/강제 구간이라는 뜻이다.
        # 미상일 때 absent 로 적으면 클라우드가 오해하므로 present 로 채우지 않는다.
        presence_state=presence_state if presence_state in ("present", "absent") else "present",
        gate_reason=decision.reason or "forced",
    )


def build_telemetry(
    *,
    device_id: UUID,
    tenant_id: str,
    seq: int,
    link: dict[str, Any],
    transport: str,
    presence_loop: dict[str, Any],
    feature_loop: dict[str, Any],
    gate_open: bool,
    signals_published: int,
    signals_gated: int,
    uptime_s: float,
    edge_version: str | None = None,
) -> TelemetryMsg:
    """링크·루프·게이트 상태를 하나로 묶는다.

    게이트가 닫혀 있었다는 사실(`gate_open`, `signals_gated`)은 **항상** 실린다 —
    무증상 침묵과 "정상적으로 조용함"을 구별할 수 있어야 하기 때문이다.
    """
    return TelemetryMsg(
        device_id=device_id,
        tenant_id=tenant_id,
        ts=_now(),
        seq=seq,
        link=LinkStats(transport=transport, **link),  # type: ignore[arg-type]
        presence_loop=LoopStats(**presence_loop),
        feature_loop=LoopStats(**feature_loop),
        gate_open=gate_open,
        signals_published=signals_published,
        signals_gated=signals_gated,
        uptime_s=uptime_s,
        edge_version=edge_version,
    )


def build_ack(
    *,
    device_id: UUID,
    tenant_id: str,
    seq: int,
    cmd_id: UUID,
    action: CmdAction,
    status: AckStatus,
    stage: str | None = None,
    progress: float | None = None,
    detail: str | None = None,
    result: dict[str, Any] | None = None,
) -> AckMsg:
    return AckMsg(
        device_id=device_id,
        tenant_id=tenant_id,
        ts=_now(),
        seq=seq,
        cmd_id=cmd_id,
        action=action,
        status=status,
        stage=stage,
        progress=progress,
        detail=detail,
        result=result or {},
    )


def to_bytes(msg: Any) -> bytes:
    """Pydantic 모델 → UTF-8 JSON. 한글 detail 이 깨지지 않게 ensure_ascii 를 쓰지 않는다."""
    return msg.model_dump_json().encode("utf-8")
