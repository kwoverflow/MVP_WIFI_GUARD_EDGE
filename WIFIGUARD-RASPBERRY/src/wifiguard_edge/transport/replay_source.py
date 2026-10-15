"""재생 전송 — Pi·수신기 없이 엣지 전 배선을 돌린다.

`CsiSource` 계약을 구현하므로 `PresenceLoop`·`FeatureLoop`·`run_calibration` 이
UART 와 구별하지 못한다. 그것이 `transport/base.py` 추상화를 둔 이유다.

두 가지 소스:
- `"synthetic"` — 합성 신호. 재실/퇴실을 주기적으로 오가며 게이팅까지 검증할 수 있다.
- 파일 경로 — 저장된 recording(.npz: `times`, `amps`) 재생.

`spi_mock.py` 와 겹치지 않는다. 저쪽은 SPI **프레임 포맷**을 흉내 내는 것이고(콜백 기반,
RingBuffer 미연결), 이쪽은 **전송 계약**을 구현해 상위 파이프라인에 실제로 물린다.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..csi.buffer import RingBuffer
from ..csi.protocol import CsiFrame

log = logging.getLogger("transport.replay")

SYNTHETIC_FS_HZ = 166.75
SYNTHETIC_SUBCARRIERS = 245
#: 재실 60초 / 퇴실 30초를 반복한다. 게이트 개폐를 실제로 관측하기 위한 값이다.
PRESENT_S = 60.0
ABSENT_S = 30.0

# ── 합성 신호 파라미터 (실측으로 정한 값 — 추정치가 아니다) ────────────────
#
# 재실 구간의 움직임은 **버스트**여야 한다. 정상 상태 사인파로는 MV 가 오르지 않는다 —
# 재실 체인이 z-score 후 이동분산을 보기 때문에 창 전체가 균일하면 분산비가 1 근처에
# 머문다. 처음에 그렇게 만들었다가 재실이 영원히 absent 였고, MV 가 0.2 에 그쳤다.
#
# 진폭을 키우는 것으로는 해결되지 않는다(z-score 가 절대 크기를 지운다). 실제로 통하는
# 두 가지는 ① 버스트가 **이동분산 창(2·omega+1 = 0.49초)보다 길 것** ② 주기가
# **presence_timeout_s(10초)보다 짧아 재발동할 것** 이다.
#
# duty(=width/period)가 작을수록 z-score 후 대비가 커진다(최대 비율 ≈ 1/duty).
# 파라미터 스윕 실측 (3.0초 창, 0.25초 스트라이드, 시드 3종 × 타이밍 지터 2종):
#   period=1.0 width=0.40 (duty 0.40) → MV max 1.37, 초과 0%   ← 버스트가 MV 창보다 짧다
#   period=3.0 width=0.80 (duty 0.27) → MV max 1.53, 초과 0%
#   period=4.0 width=1.60 (duty 0.40) → MV max 3.00, p90 2.12  ← 여유 부족. 전체 앱에서
#                                        피처 루프 CPU 경합이 끼면 1.71 까지 떨어져 실패했다
#   period=4.0 width=1.00 (duty 0.25) → MV max 8.01, p90 2.58, 최대 공백 4.0초  ✓ 채택
BURST_PERIOD_S = 4.0
BURST_WIDTH_S = 1.0
BURST_AMPLITUDE = 8.0

# 배경 잡음은 **평활해야** 한다. 백색잡음을 그대로 쓰면 퇴실 구간에서도 MV 가 2.79 까지
# 튀어 오탐이 난다 — `select_top_subcarriers_2d` 가 하필 "최대/평균 분산비가 가장 큰"
# 서브캐리어 10개를 고르므로, 245개 중 우연히 가장 크게 튄 것들이 선택되기 때문이다.
# 21탭 이동평균에 해당하는 EMA 를 걸면 퇴실 MV 최대가 1.30 으로 떨어지고 오탐이 0% 가 된다.
NOISE_STD = 0.08
NOISE_EMA_ALPHA = 2.0 / (21 + 1)  # 21탭 이동평균 등가


class ReplaySource(threading.Thread):
    """합성 또는 저장된 CSI 를 실시간 속도로 RingBuffer 에 흘려 넣는다."""

    def __init__(
        self,
        buffer: RingBuffer,
        source: str = "synthetic",
        speed: float = 1.0,
        *,
        fs_hz: float = SYNTHETIC_FS_HZ,
        subcarriers: int = SYNTHETIC_SUBCARRIERS,
        seed: int = 11,
    ) -> None:
        super().__init__(daemon=True, name="csi-replay")
        self.buffer = buffer
        self.source = source
        self.speed = max(speed, 0.01)
        self.fs_hz = fs_hz
        self.subcarriers = subcarriers
        self._rng = np.random.default_rng(seed)
        # ``threading.Thread`` already owns a private ``_stop()`` method that
        # ``join()`` calls after the worker exits.  Shadowing it with an Event
        # makes an otherwise clean shutdown fail with ``Event is not callable``.
        self._stop_event = threading.Event()
        self._frames_emitted = 0
        self._t0 = time.monotonic()
        self._recording: tuple[np.ndarray, np.ndarray] | None = None
        if source != "synthetic":
            self._recording = self._load(Path(source))
            self.subcarriers = self._recording[1].shape[1]
        # 합성 신호의 고정 상태 — 매 프레임 새로 뽑으면 서브캐리어 간 구조가 사라져
        # PCA 가 의미 있는 주성분을 못 찾는다.
        self._noise = np.zeros(self.subcarriers)
        self._burst_gains = self._rng.uniform(0.4, 1.0, size=self.subcarriers)
        self._burst_phase = np.linspace(0.0, 4.0, self.subcarriers)
        self._wander_phase = np.linspace(0.0, 1.0, self.subcarriers)

    # ── CsiSource 계약 ──────────────────────────────────────────────
    def stop(self) -> None:
        self._stop_event.set()

    @property
    def running(self) -> bool:
        return not self._stop_event.is_set() and self.is_alive()

    @property
    def packet_count(self) -> int:
        return self._frames_emitted

    def get_window(self, seconds: float):
        return self.buffer.get_window(seconds)

    def send_line(self, text: str) -> bool:
        """재생에는 수신기가 없다. 캘리브레이션이 `"train"` 을 보내도 성공으로 답한다 —
        그래야 캘리브레이션 흐름 전체를 하드웨어 없이 검증할 수 있다."""
        log.info("[replay] send_line(%r) — 무시하고 성공 처리", text)
        return True

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.running,
            "port": f"replay:{self.source}",
            "baud": None,
            "reconnects": 0,
            "frames_ok": self._frames_emitted,
            "checksum_errors": 0,
            "resyncs": 0,
            "mac_filtered": 0,
        }

    # ── 생성 ────────────────────────────────────────────────────────
    def _load(self, path: Path) -> tuple[np.ndarray, np.ndarray]:
        if not path.exists():
            raise FileNotFoundError(f"recording 이 없다: {path}")
        data = np.load(path)
        for key in ("times", "amps"):
            if key not in data:
                raise ValueError(f"{path} 에 '{key}' 배열이 없다 (필요: times, amps)")
        times = np.asarray(data["times"], dtype=np.float64)
        amps = np.asarray(data["amps"], dtype=np.float32)
        log.info("recording 로드: %s (%d 프레임, %d 서브캐리어)", path, len(times), amps.shape[1])
        return times, amps

    def _synthetic_amps(self, elapsed_s: float) -> np.ndarray:
        """재실/퇴실 구간에 따라 진폭 변동 폭을 바꾼다.

        재실 구간은 **버스트**(약 0.4초 활동 + 0.6초 정적)를 반복한다. 상수 진폭 사인파는
        z-score 후 이동분산이 평탄해져 MV 가 임계값을 넘지 못한다 — 위 BURST_* 주석 참조.
        퇴실 구간은 잔잔한 잡음만 남긴다. 게이트가 실제로 열리고 닫히는지 보려는 것이다.
        """
        phase = elapsed_s % (PRESENT_S + ABSENT_S)
        present = phase < PRESENT_S

        # 배경: 느린 드리프트 + EMA 로 평활한 잡음 (퇴실 구간은 이것만)
        a = NOISE_EMA_ALPHA
        white = self._rng.normal(0, NOISE_STD * np.sqrt((2 - a) / a), size=self.subcarriers)
        self._noise = (1 - a) * self._noise + a * white
        amps = 20.0 + 0.5 * np.sin(2 * np.pi * 0.03 * elapsed_s) + self._noise
        if not present:
            return amps.astype(np.float32)

        # 재실: 주기적 버스트. 창 끝이 버스트 안에 들어오면 MV 가 임계값을 넘고,
        # presence_timeout_s(10초) 덕분에 버스트 사이에도 PRESENT 가 유지된다.
        if (elapsed_s % BURST_PERIOD_S) < BURST_WIDTH_S:
            envelope = np.sin(np.pi * (elapsed_s % BURST_PERIOD_S) / BURST_WIDTH_S)  # 0→1→0
            amps += (
                BURST_AMPLITUDE * envelope * self._burst_gains
                * np.sin(2 * np.pi * 2.3 * elapsed_s + self._burst_phase)
            )
            amps += self._rng.normal(0, envelope, size=self.subcarriers)

        # 저주파 미세움직임 (wander 축) — 재실 구간에만
        amps += 0.35 * np.sin(2 * np.pi * 0.25 * elapsed_s + self._wander_phase)
        return amps.astype(np.float32)

    def run(self) -> None:
        log.info(
            "replay 시작: source=%s speed=%.2f fs=%.2fHz subcarriers=%d",
            self.source, self.speed, self.fs_hz, self.subcarriers,
        )
        period = 1.0 / (self.fs_hz * self.speed)
        idx = 0
        next_at = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_at:
                time.sleep(min(next_at - now, 0.02))
                continue
            next_at += period

            elapsed = now - self._t0
            if self._recording is not None:
                times, amps_all = self._recording
                pos = idx % len(times)
                amps = amps_all[pos]
            else:
                amps = self._synthetic_amps(elapsed)

            # 장치 클럭은 마이크로초. RingBuffer 가 랩·리셋을 처리하므로 단조 증가만 지키면 된다.
            self.buffer.append(
                CsiFrame(
                    seq=idx,
                    mac="1a:00:00:00:00:00",
                    rssi=-45,
                    noise_floor=-92,
                    channel=48,
                    timestamp_us=int(elapsed * 1e6) & 0xFFFFFFFF,
                    fft_gain=0,
                    agc_gain=0,
                    csi_len=len(amps) * 2,
                    amps=amps,
                    host_time=now,
                )
            )
            self._frames_emitted += 1
            idx += 1
