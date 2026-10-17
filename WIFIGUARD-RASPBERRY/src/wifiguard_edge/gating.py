"""업로드 게이트 — 어느 윈도우를 클라우드로 올릴지 결정한다 (F-P11).

이 파일 하나가 클라우드 비용과 대역폭을 정한다. 신호는 약 2KB 지만 4Hz 로 상시 올리면
기기당 96kbps 이고, 더 큰 문제는 **클라우드 CWT 비용**이다 — 모델서버 1코어가 초당 약 2윈도우를
처리하는데 기기당 4윈도우가 필요하므로, 게이팅 없이는 코어당 0.5기기다. 재실률 30% 가정 시
게이트가 이를 3.3배로 늘린다. 비용 0의 가장 큰 레버다.

## 두 가지 안전 규칙

1. **ABSENT 직후 바로 닫지 않는다** (`linger_after_absent_s`). 낙상하면 사람이 움직이지
   않아 재실이 ABSENT 로 떨어진다. 곧바로 닫으면 정작 필요한 구간을 잃는다.
2. **미상(unknown)은 ABSENT 가 아니다.** 기동 직후 재실 루프가 아직 한 틱도 돌지 않은
   상태에서 닫아 버리면 초기 구간을 통째로 잃는다. 기본은 올리고 `gate_reason="forced"` 로 남긴다.

게이트가 닫혀 있었다는 사실 자체는 `TelemetryMsg.signals_gated` 로 **항상** 보고한다.
무증상 침묵과 "정상적으로 조용함"을 구별할 수 있어야 하기 때문이다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Literal

GateReason = Literal["present", "forced", "calibration"]
"""`wifiguard_contracts.mqtt.SignalMsg.gate_reason` 과 같은 값이어야 한다."""


@dataclass(frozen=True)
class GateDecision:
    open: bool
    reason: GateReason | None  # open=False 면 None
    detail: str  # 관측용 — 왜 이렇게 판단했는가


class SignalGate:
    """재실 상태를 받아 signal 발행 여부를 정한다. 스레드 안전."""

    def __init__(
        self,
        *,
        linger_after_absent_s: float = 10.0,
        publish_when_unknown: bool = True,
        clock=time.monotonic,
    ) -> None:
        self._linger = linger_after_absent_s
        self._publish_when_unknown = publish_when_unknown
        self._clock = clock
        self._lock = threading.Lock()
        self._last_present_at: float | None = None
        self._forced_until: float | None = None
        self._opened = 0
        self._gated = 0

    # ── 입력 ────────────────────────────────────────────────────────
    def note_presence(self, state: str | None) -> None:
        """재실 루프가 매 틱 호출한다. `state` 는 "present"/"absent"/None(미상)."""
        if state == "present":
            with self._lock:
                self._last_present_at = self._clock()

    def force_open(self, seconds: float, reason: GateReason = "calibration") -> None:
        """캘리브레이션처럼 재실과 무관하게 반드시 올려야 하는 구간을 연다."""
        del reason  # 현재는 calibration 만 쓴다. 시그니처는 확장 여지로 남긴다.
        with self._lock:
            self._forced_until = self._clock() + seconds

    # ── 판정 ────────────────────────────────────────────────────────
    def decide(self, state: str | None) -> GateDecision:
        """이 윈도우를 올릴 것인가. 호출할 때마다 카운터가 증가한다."""
        now = self._clock()
        with self._lock:
            if self._forced_until is not None and now < self._forced_until:
                self._opened += 1
                return GateDecision(True, "calibration", "강제 개방 구간")

            if state == "present":
                self._last_present_at = now
                self._opened += 1
                return GateDecision(True, "present", "재실")

            if state is None:
                if self._publish_when_unknown:
                    self._opened += 1
                    return GateDecision(True, "forced", "재실 미상 — 초기 구간 유실 방지")
                self._gated += 1
                return GateDecision(False, None, "재실 미상 · publish_when_unknown=false")

            # absent
            last = self._last_present_at
            if last is not None and (now - last) < self._linger:
                self._opened += 1
                remaining = self._linger - (now - last)
                return GateDecision(True, "forced", f"ABSENT 유예 {remaining:.1f}s 남음")
            self._gated += 1
            return GateDecision(False, None, "퇴실")

    # ── 관측 ────────────────────────────────────────────────────────
    @property
    def is_open(self) -> bool:
        """마지막 판정과 무관하게 지금 열려 있는지 (텔레메트리 `gate_open`)."""
        now = self._clock()
        with self._lock:
            if self._forced_until is not None and now < self._forced_until:
                return True
            last = self._last_present_at
            return last is not None and (now - last) < self._linger

    def counters(self) -> tuple[int, int]:
        """`(signals_published, signals_gated)` — 텔레메트리로 나간다."""
        with self._lock:
            return self._opened, self._gated
