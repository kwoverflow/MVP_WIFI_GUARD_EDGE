"""최신값 캐시 + WS 팬아웃 허브.

## LiveCache — REST 가 동기로 읽는다

`ResidentOut` 의 런타임 7필드(`state`/`mv`/`wander`/`presence`/…)를 채우려면 REST 핸들러가
"이 기기의 마지막 재실 상태"를 즉시 알아야 한다. DB 를 조회하면 4Hz 쓰기에 읽기가 얹혀
비싸고, 어차피 필요한 건 **마지막 값 하나**다. 그래서 인메모리 dict 로 둔다.

캐시가 비어 있으면 필드는 `None` 이고, 프론트는 그것을 **"감지 미동작"** 으로 표시한다.
"퇴실"과 구별되어야 하는 값이므로 기본값을 `absent` 로 채우면 안 된다.

## LiveHub — 컨슈머 스레드 → asyncio WS

Kafka 컨슈머는 스레드에서 돌고 WebSocket 은 asyncio 다. 스레드에서 직접
`await queue.put()` 을 할 수 없으므로 `loop.call_soon_threadsafe` 로 넘긴다.

느린 구독자가 전체를 막으면 안 되므로 구독자별 큐에 상한을 두고, 넘치면 **가장 오래된
것부터 버린다**. 실시간 화면에서 오래된 프레임은 가치가 없다.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

log = logging.getLogger("ingest.cache")

#: 구독자당 보류 이벤트 상한. 10Hz 기준 3초치.
SUB_QUEUE_MAX = 32


@dataclass
class DeviceState:
    """한 기기의 최신 상태. 없는 축은 None 으로 남는다."""

    device_id: UUID
    tenant_id: str
    presence: dict[str, Any] | None = None
    presence_at: datetime | None = None
    fall: dict[str, Any] | None = None
    fall_at: datetime | None = None
    link: dict[str, Any] | None = None
    last_seen_at: datetime | None = None
    #: 마지막 텔레메트리 수신 시각(monotonic). online 판정용 — wall clock 은 기기 시계가
    #: 틀어지면 못 믿는다.
    _seen_mono: float | None = field(default=None, repr=False)

    def online(self, offline_after_s: float) -> bool | None:
        """None = 모름(텔레메트리를 한 번도 못 받음). False = 끊김. 둘은 다른 상태다."""
        if self._seen_mono is None:
            return None
        return (time.monotonic() - self._seen_mono) < offline_after_s


class LiveCache:
    """기기별 최신 상태. 스레드 안전 (컨슈머 스레드가 쓰고 REST 스레드가 읽는다)."""

    def __init__(self, offline_after_s: float = 15.0) -> None:
        self._lock = threading.Lock()
        self._by_device: dict[UUID, DeviceState] = {}
        self.offline_after_s = offline_after_s

    def _slot(self, device_id: UUID, tenant_id: str) -> DeviceState:
        state = self._by_device.get(device_id)
        if state is None:
            state = DeviceState(device_id=device_id, tenant_id=tenant_id)
            self._by_device[device_id] = state
        return state

    # ── 쓰기 (컨슈머 스레드) ────────────────────────────────────────
    def apply_presence(self, device_id: UUID, tenant_id: str, fields: dict[str, Any], ts: datetime) -> DeviceState:
        with self._lock:
            s = self._slot(device_id, tenant_id)
            s.presence = fields
            s.presence_at = ts
            return _copy(s)

    def apply_telemetry(self, device_id: UUID, tenant_id: str, link: dict[str, Any], ts: datetime, *, input_required: bool = False) -> DeviceState:
        with self._lock:
            s = self._slot(device_id, tenant_id)
            s.link = link
            s.last_seen_at = ts
            # Original broker mode measures edge heartbeat. Local Pi5 mode also
            # requires fresh CSI: the API/relay can be alive with the sender off.
            source_missing = input_required and link.get("connected") is not True
            s._seen_mono = float("-inf") if source_missing else time.monotonic()
            if source_missing:
                s.presence = s.fall = None
                s.presence_at = s.fall_at = None
            return _copy(s)

    def apply_fall(self, device_id: UUID, tenant_id: str, fields: dict[str, Any], ts: datetime) -> DeviceState:
        with self._lock:
            s = self._slot(device_id, tenant_id)
            s.fall = fields
            s.fall_at = ts
            return _copy(s)

    def pause_fall(self, device_id: UUID, tenant_id: str, *, clear_presence: bool = False) -> DeviceState:
        """Clear stale live inference only; persisted fall events are untouched."""
        with self._lock:
            s = self._slot(device_id, tenant_id)
            s.fall = s.fall_at = None
            if clear_presence:
                s.presence = s.presence_at = None
            return _copy(s)

    # ── 읽기 (REST/WS 스레드) ───────────────────────────────────────
    def get(self, device_id: UUID) -> DeviceState | None:
        with self._lock:
            s = self._by_device.get(device_id)
            return _copy(s) if s else None

    def for_devices(self, device_ids: list[UUID]) -> dict[UUID, DeviceState]:
        with self._lock:
            return {d: _copy(self._by_device[d]) for d in device_ids if d in self._by_device}

    def for_tenant(self, tenant_id: str) -> dict[UUID, DeviceState]:
        with self._lock:
            return {d: _copy(s) for d, s in self._by_device.items() if s.tenant_id == tenant_id}

    def snapshot(self) -> dict[UUID, DeviceState]:
        """전 기기 사본. 오프라인 스윕처럼 테넌트와 무관한 순회에 쓴다."""
        with self._lock:
            return {d: _copy(s) for d, s in self._by_device.items()}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._by_device)
            online = sum(1 for s in self._by_device.values() if s.online(self.offline_after_s))
        return {"devices": total, "online": online}


def _copy(s: DeviceState) -> DeviceState:
    """호출자가 들고 나가는 사본. 락 밖에서 변형돼도 캐시가 오염되지 않는다."""
    return DeviceState(
        device_id=s.device_id,
        tenant_id=s.tenant_id,
        presence=dict(s.presence) if s.presence else None,
        presence_at=s.presence_at,
        fall=dict(s.fall) if s.fall else None,
        fall_at=s.fall_at,
        link=dict(s.link) if s.link else None,
        last_seen_at=s.last_seen_at,
        _seen_mono=s._seen_mono,
    )


class Subscription:
    """WS 연결 하나. `device_ids` 가 비면 테넌트 전체를 받는다."""

    def __init__(self, tenant_id: str, device_ids: set[UUID] | None = None) -> None:
        self.tenant_id = tenant_id
        self.device_ids = device_ids or set()
        self.queue: asyncio.Queue[DeviceState] = asyncio.Queue(maxsize=SUB_QUEUE_MAX)
        self.dropped = 0

    def wants(self, state: DeviceState) -> bool:
        if state.tenant_id != self.tenant_id:
            return False
        return not self.device_ids or state.device_id in self.device_ids

    def offer(self, state: DeviceState) -> None:
        """asyncio 스레드에서만 호출된다. 가득 차면 **가장 오래된 것**을 버린다."""
        try:
            self.queue.put_nowait(state)
            return
        except asyncio.QueueFull:
            pass
        try:
            self.queue.get_nowait()  # 오래된 프레임은 실시간 화면에서 가치가 없다
            self.queue.put_nowait(state)
        except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
            pass
        self.dropped += 1


class LiveHub:
    """컨슈머 스레드의 갱신을 구독 중인 WS 연결로 팬아웃한다."""

    def __init__(self) -> None:
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """lifespan 이 기동 시 한 번 호출한다. 이게 없으면 팬아웃이 조용히 사라진다."""
        self._loop = loop

    def subscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs.add(sub)

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish_from_thread(self, state: DeviceState) -> None:
        """컨슈머 스레드가 호출한다. 이벤트 루프에 넘기고 즉시 반환한다."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            targets = [s for s in self._subs if s.wants(state)]
        if not targets:
            return
        try:
            loop.call_soon_threadsafe(self._deliver, targets, state)
        except RuntimeError:  # 루프가 종료 중
            pass

    @staticmethod
    def _deliver(targets: list[Subscription], state: DeviceState) -> None:
        for sub in targets:
            sub.offer(state)
