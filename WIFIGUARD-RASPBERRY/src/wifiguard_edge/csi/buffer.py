"""수신 CSI 프레임 링버퍼와 수신 품질 지표.

ESP32 타임스탬프(us, 2^32 랩)를 단조 증가 초 단위로 unwrap 해서 보관한다.
모델 입력(3초 윈도우, 0.25초 스트라이드)을 충분히 덮도록 기본 30초 분량 유지.
"""

from __future__ import annotations

import threading
from collections import deque

import numpy as np

from .protocol import CsiFrame

_TS_WRAP = 1 << 32  # us


class RingBuffer:
    def __init__(self, max_seconds: float = 30.0, nominal_hz: float = 200.0) -> None:
        maxlen = int(max_seconds * nominal_hz * 1.5)
        self._lock = threading.Lock()
        self._frames: deque[CsiFrame] = deque(maxlen=maxlen)
        self._times: deque[float] = deque(maxlen=maxlen)  # unwrap 된 장치 시각(초)
        self._last_raw_ts: int | None = None
        self._ts_offset = 0  # 누적 wrap 보정 (us)
        self.total_frames = 0
        self.device_resets = 0  # 타임스탬프 리셋(수신기 재부팅) 감지 횟수
        self.max_seconds = max_seconds

    def append(self, frame: CsiFrame) -> None:
        with self._lock:
            raw = frame.timestamp_us
            if self._last_raw_ts is not None and raw < self._last_raw_ts - (_TS_WRAP // 2):
                self._ts_offset += _TS_WRAP
            self._last_raw_ts = raw
            t = (raw + self._ts_offset) / 1e6
            if self._times and not (self._times[-1] - 0.5 <= t <= self._times[-1] + 60.0):
                # 장치 재부팅으로 타임스탬프가 리셋됨 (뒤로 점프, 또는 uptime이
                # 랩 한계를 넘긴 재부팅이 랩으로 오인돼 크게 앞으로 점프).
                # 이전 epoch 프레임과 섞이면 윈도우가 오염되므로 버퍼를 비운다.
                self._frames.clear()
                self._times.clear()
                self._ts_offset = 0
                self.device_resets += 1
                t = raw / 1e6
            self._frames.append(frame)
            self._times.append(t)
            self.total_frames += 1

    def stats(self) -> dict:
        with self._lock:
            n = len(self._frames)
            if n == 0:
                return {
                    "buffered_frames": 0,
                    "buffered_seconds": 0.0,
                    "hz_1s": 0.0,
                    "hz_5s": 0.0,
                    "last_rssi": None,
                    "last_agc_gain": None,
                    "subcarriers": None,
                    "total_frames": self.total_frames,
                    "device_resets": self.device_resets,
                }
            times = self._times
            latest = times[-1]
            hz_1s = sum(1 for t in times if t > latest - 1.0) / 1.0
            span5 = min(5.0, max(latest - times[0], 1e-9))
            hz_5s = sum(1 for t in times if t > latest - 5.0) / span5
            last = self._frames[-1]
            return {
                "buffered_frames": n,
                "buffered_seconds": round(latest - times[0], 2),
                "hz_1s": round(hz_1s, 1),
                "hz_5s": round(hz_5s, 1),
                "last_rssi": last.rssi,
                "last_agc_gain": last.agc_gain,
                "subcarriers": int(last.csi_len // 2),
                "total_frames": self.total_frames,
                "device_resets": self.device_resets,
            }

    def window(self, seconds: float) -> tuple[np.ndarray, np.ndarray]:
        """최근 seconds 구간을 (시각 배열, 진폭 행렬[frames x subcarriers])로 반환."""
        with self._lock:
            if not self._frames:
                return np.empty(0), np.empty((0, 0), dtype=np.float32)
            latest = self._times[-1]
            cut = latest - seconds
            idx = [i for i, t in enumerate(self._times) if t >= cut]
            if not idx:
                return np.empty(0), np.empty((0, 0), dtype=np.float32)
            start = idx[0]
            times = np.array(list(self._times)[start:])
            n_sc = min(f.amps.shape[0] for f in list(self._frames)[start:])
            amps = np.stack([f.amps[:n_sc] for f in list(self._frames)[start:]])
            return times, amps

    def get_window(self, seconds: float) -> tuple[np.ndarray, np.ndarray] | None:
        """`window()`의 별칭 — 온보딩 캘리브레이션(onboarding.run_calibration)의
        monitor 덕타이핑 계약(migration.md §5)이 기대하는 이름. 데이터가 없으면
        `window()`처럼 빈 배열 대신 None을 반환해 호출부가 "윈도우 없음"과
        "빈 윈도우"를 구분하지 않고 바로 None 체크만 하면 되게 한다."""
        times, amps = self.window(seconds)
        if times.size == 0:
            return None
        return times, amps
