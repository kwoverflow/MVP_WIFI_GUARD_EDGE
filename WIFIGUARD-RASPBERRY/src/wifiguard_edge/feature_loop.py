"""FeatureLoop — 0.25초마다 1-D 합성 대표신호를 만들어 발행 큐로 넘긴다.

`PresenceLoop` 와 같은 스케줄러 패턴이지만 **완전히 독립적인 스레드**다. 재실감지는
네트워크·클라우드와 무관하게 끊기지 않아야 하고(G7), 이 루프는 게이트가 닫히면 아무 일도
하지 않는다. 한쪽이 죽어도 다른 쪽은 돌아야 하므로 스레드를 합치지 않는다.

## 단위 함정

이 루프는 `times` 를 **초** 단위로 그대로 쓴다. `PresenceLoop` 는 같은 링버퍼 출력을
**마이크로초**로 바꿔 넘긴다(`presence_loop.py:88,108` 의 `* 1e6`) — 재실 체인이
`compute_final_signal(timestamps_us, ...)` 를 요구하기 때문이다. 두 체인이 같은 버퍼를
읽으면서 단위 규약이 다르므로, 변환은 각 루프 안에서만 하고 밖으로 새지 않게 한다.
`tests/test_units.py` 가 이를 고정한다.

## 발행은 이 스레드를 막지 않는다

`sink` 는 큐에 넣기만 하고 즉시 반환해야 한다. MQTT 왕복이 여기서 일어나면 발행 지연이
그대로 피처 생성 지연이 되고, 결국 윈도우를 놓친다. `mqtt.publisher.MqttPublisher.enqueue`
가 그 계약을 지킨다(큐가 차면 오래된 signal 부터 버린다).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .csi.buffer import RingBuffer
from .features.realtime import FeatureConfig, WindowSignal, extract_window_signal
from .gating import GateDecision, SignalGate

log = logging.getLogger("feature_loop")

#: 모델 학습 시 stride 와 같아야 한다 (services/inference state_machine.STRIDE_SEC).
STRIDE_SEC = 0.25

#: 링버퍼에서 꺼낼 여유분. window_seconds 정확히 요청하면 경계에서 자주 미달한다.
WINDOW_SLACK_S = 0.5

SignalSink = Callable[[WindowSignal, GateDecision], None]


class FeatureLoop(threading.Thread):
    def __init__(
        self,
        ring: RingBuffer,
        config: FeatureConfig,
        gate: SignalGate,
        sink: SignalSink,
        *,
        stride_sec: float = STRIDE_SEC,
        presence_state: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="feature-loop")
        self.ring = ring
        self.config = config
        self.gate = gate
        self.sink = sink
        self.stride_sec = stride_sec
        #: 재실 상태 조회 콜백. `PresenceLoop` 와 직접 결합하지 않으려고 함수로 받는다 —
        #: 재생·테스트에서 재실 루프 없이도 이 루프를 돌릴 수 있어야 한다.
        self._presence_state = presence_state or (lambda: None)

        # threading.Thread.join() calls its private _stop() method.  Keep our
        # event under a different name so a clean service shutdown can join.
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._tick_count = 0
        self._skip_count = 0
        self._last_error: str | None = None
        self._last_signal_at: float | None = None
        self._seq = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        log.info("feature loop start: stride=%.2fs", self.stride_sec)
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.05))
                continue
            next_tick = max(next_tick + self.stride_sec, now)
            try:
                self._tick()
            except Exception:  # 한 윈도우 실패가 루프를 죽이면 안 된다
                log.exception("feature tick failed")
                self._record_skip("unhandled exception")

    def _tick(self) -> None:
        state = self._presence_state()
        decision = self.gate.decide(state)
        if not decision.open:
            self._record_skip(f"gated: {decision.detail}")
            return

        window = self.ring.get_window(self.config.window_seconds + WINDOW_SLACK_S)
        if window is None:
            self._record_skip("no data")
            return

        times_s, amplitude = window  # ★ 초 단위 그대로. 재실 체인과 달리 1e6 을 곱하지 않는다.
        try:
            signal = extract_window_signal(times_s, amplitude, self.config)
        except ValueError as exc:
            # 윈도우가 짧거나 fs 가 이상한 경우 — 정상적으로 흔하다(기동 직후 등)
            self._record_skip(str(exc))
            return

        with self._lock:
            self._tick_count += 1
            self._seq += 1
            self._last_error = None
            self._last_signal_at = time.time()
        self.sink(signal, decision)

    def _record_skip(self, reason: str) -> None:
        with self._lock:
            self._skip_count += 1
            self._last_error = reason

    def next_seq(self) -> int:
        with self._lock:
            return self._seq

    def status(self) -> dict[str, Any]:
        """`wifiguard_contracts.mqtt.LoopStats` 로 옮겨진다."""
        with self._lock:
            return {
                "enabled": True,
                "tick_count": self._tick_count,
                "skip_count": self._skip_count,
                "last_error": self._last_error,
            }
