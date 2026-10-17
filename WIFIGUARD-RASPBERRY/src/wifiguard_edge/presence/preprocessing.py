"""재실 감지 신호체인이 쓰는 저수준 신호처리 함수.

reference/fall_detect/src/preprocessing.py(amfall 기반 CSI 전처리)에서 재실 감지가
실제로 쓰는 4개 함수만 이식했다(migration.md §3 의존성 표 참고) — 원본의 3D 안테나
파이프라인(select_streams/select_pcs/preprocess_csi 등)은 낙상 DL 모델과 무관하고
이식 대상에서도 쓰이지 않아 제외했다.
"""

from typing import Any, Dict, Tuple
from functools import lru_cache

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy import signal


def _moving_variance(x: np.ndarray, W: int) -> np.ndarray:
    """
    Compute moving variance using efficient convolution.

    Per AmFall spec: υ(n; h) = (1/2W) * Σ_{k=n-W}^{n+W} (||h[k]|| - μ_h(n))²
    Denominator is 2W (window runs from n-W to n+W but counts as 2W for normalization).
    """
    size = 2 * W + 1
    mu = uniform_filter1d(x, size=size, mode="nearest")
    mu2 = uniform_filter1d(x**2, size=size, mode="nearest")
    variance = mu2 - mu**2
    if W > 0:
        variance = variance * (2 * W + 1) / (2 * W)
    return variance


def _compute_q(signal_1d: np.ndarray, omega: int) -> float:
    """q metric: max(moving_variance) / mean(moving_variance) — activity sensitivity."""
    upsilon = _moving_variance(np.abs(signal_1d), omega)
    mean_v = np.mean(upsilon)
    if mean_v < 1e-10:
        return 0.0
    return float(np.max(upsilon) / mean_v)


def resample_signal(
    timestamps: np.ndarray,
    signal_arr: np.ndarray,
    fs_hz: float = 320.0,
    tolerance_ms: float = 2.0,
    max_interp_gap_steps: int = 64,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Resample signal to a regular grid using linear interpolation.

    timestamps must be in microseconds (irregular), shape (N,). signal_arr can be
    any shape after the first (time) dim. Handles irregular gaps by interpolating
    when the gap fits within tolerance, falling back to a copy otherwise.
    """
    if len(signal_arr) == 0:
        return signal_arr.astype(np.float32), {
            "raw_samples": 0,
            "resampled_samples": 0,
            "interp_steps": 0,
            "fallback_steps": 0,
            "irregular_gaps": 0,
            "nonpositive_gaps": 0,
        }

    timestamps = timestamps.astype(np.float64)
    signal_arr = signal_arr.astype(np.float32)

    grid_us = 1_000_000.0 / fs_hz
    tolerance_us = tolerance_ms * 1000.0

    # Common native 320 Hz input needs no inserted rows in this legacy chain.
    # Avoid thousands of Python row copies without changing values or counters.
    # np.rint and Python round both use ties-to-even for these float64 gaps.
    gaps = np.diff(timestamps)
    if len(timestamps) == len(signal_arr) and np.isfinite(gaps).all():
        steps = np.maximum(1.0, np.rint(gaps / grid_us))
        positive = gaps > 0
        interpolatable = (
            positive & (steps <= max_interp_gap_steps)
            & (np.abs(gaps - steps * grid_us) <= tolerance_us)
        )
        if not np.any(interpolatable & (steps > 1)):
            return signal_arr.copy(), {
                "raw_samples": len(signal_arr),
                "resampled_samples": len(signal_arr),
                "interp_steps": 0,
                "fallback_steps": int(np.count_nonzero(~interpolatable)),
                "irregular_gaps": int(np.count_nonzero(positive & ~interpolatable)),
                "nonpositive_gaps": int(np.count_nonzero(~positive)),
            }

    rows = [signal_arr[0].astype(np.float32)]
    interp_steps = 0
    fallback_steps = 0
    irregular_gaps = 0
    nonpositive_gaps = 0

    for i in range(1, len(signal_arr)):
        gap = float(timestamps[i] - timestamps[i - 1])

        if gap <= 0:
            rows.append(signal_arr[i].astype(np.float32))
            fallback_steps += 1
            nonpositive_gaps += 1
            continue

        nearest_steps = max(1, int(round(gap / grid_us)))
        can_interp = (
            nearest_steps <= max_interp_gap_steps
            and abs(gap - nearest_steps * grid_us) <= tolerance_us
        )

        if can_interp:
            for step in range(1, nearest_steps):
                alpha = step / nearest_steps
                interpolated = ((1.0 - alpha) * signal_arr[i - 1] + alpha * signal_arr[i]).astype(
                    np.float32
                )
                rows.append(interpolated)
                interp_steps += 1
            rows.append(signal_arr[i].astype(np.float32))
        else:
            rows.append(signal_arr[i].astype(np.float32))
            fallback_steps += 1
            irregular_gaps += 1

    resampled = np.stack(rows, axis=0).astype(np.float32)

    stats = {
        "raw_samples": int(len(signal_arr)),
        "resampled_samples": int(len(resampled)),
        "interp_steps": int(interp_steps),
        "fallback_steps": int(fallback_steps),
        "irregular_gaps": int(irregular_gaps),
        "nonpositive_gaps": int(nonpositive_gaps),
    }

    return resampled, stats


@lru_cache(maxsize=32)
def _bandpass_coefficients(fs_hz, low_hz, high_hz, order):
    # Cache only static filter design, never a signal/window/calibration result.
    coefficients = signal.butter(order, [low_hz, high_hz], btype="band", fs=fs_hz, output="sos")
    coefficients.flags.writeable = False
    return coefficients


def bandpass_filter(
    amp: np.ndarray,
    fs_hz: float = 320.0,
    low_hz: float = 0.5,
    high_hz: float = 150.0,
    order: int = 4,
) -> np.ndarray:
    """Zero-phase Butterworth bandpass filter (sosfiltfilt) applied along axis 0."""
    # scipy's native SOS filter requires writable coefficients; do not expose
    # or mutate the cached array, including concurrent motion/wander calls.
    sos = _bandpass_coefficients(fs_hz, low_hz, high_hz, order).copy()
    filtered = signal.sosfiltfilt(sos, amp, axis=0)
    return filtered.astype(np.float32)
