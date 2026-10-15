"""재실 감지용 CSI 신호체인 — 낙상 DL 모델(features/realtime.py)과 완전히 별개 경로.

reference/fall_detect/src/streaming_features.py를 그대로 이식했다(migration.md §3).
resample → bandpass → 부반송파 선택 → 합산/정규화 → (MV 패스) 이동분산 또는
(Wander 패스) Welch PSD 밴드 에너지, 두 갈래로 갈라진다.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np
from scipy import signal

from .preprocessing import _compute_q, _moving_variance, bandpass_filter, resample_signal


@dataclass
class FinalSignalResult:
    """Complete per-tick pipeline output."""

    final_signal: np.ndarray  # (N,) z-scored signal
    moving_variance: np.ndarray  # (N,) moving variance of final_signal
    mv_current: float  # Last value of moving_variance (scalar for state machine)
    selected_indices: np.ndarray  # (n_selected,) indices of selected subcarriers
    q_values: np.ndarray  # (n_selected,) q-values of selected subcarriers
    resample_stats: dict  # From resample_signal
    window_sample_count: int
    window_duration_s: float
    band_energy: float | None = None  # Welch PSD band energy (only set if requested)


def select_top_subcarriers_2d(
    amp_2d: np.ndarray, omega: int, n_streams: int = 10
) -> Tuple[np.ndarray, np.ndarray]:
    """Select top N subcarriers by q-value from a 2D amplitude array (no Q-threshold pruning)."""
    n_samples, n_subcarriers = amp_2d.shape
    n_select = min(n_streams, n_subcarriers)

    q_values = np.zeros(n_subcarriers, dtype=np.float32)
    for i in range(n_subcarriers):
        q_values[i] = _compute_q(amp_2d[:, i], omega)

    top_indices = np.argsort(-q_values)[:n_select]
    top_q_values = q_values[top_indices]

    return top_indices, top_q_values


def safe_bandpass(
    signal_arr: np.ndarray,
    fs_hz: float,
    low_hz: float = 2.0,
    high_hz: float = 50.0,
    order: int = 4,
    nyquist_margin_hz: float = 1.0,
) -> Tuple[np.ndarray, dict]:
    """Bandpass filter with Nyquist-edge safety; skips filtering if the signal is too short."""
    nyquist_hz = fs_hz / 2.0
    high_hz_actual = min(high_hz, nyquist_hz - nyquist_margin_hz)

    min_signal_len = 2 * max(order, 2) + 1
    if len(signal_arr) < min_signal_len:
        return signal_arr.astype(np.float32), {
            "filter_skipped": True,
            "high_hz_clamped": high_hz_actual,
            "reason": "signal_too_short",
        }

    try:
        filtered = bandpass_filter(
            signal_arr, fs_hz=fs_hz, low_hz=low_hz, high_hz=high_hz_actual, order=order
        )
        return filtered, {"filter_skipped": False, "high_hz_clamped": high_hz_actual}
    except Exception as e:
        return signal_arr.astype(np.float32), {
            "filter_skipped": True,
            "high_hz_clamped": high_hz_actual,
            "error": str(e),
        }


def sum_and_normalize(amp_2d: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Sum selected subcarrier columns and z-score normalize."""
    combined = amp_2d[:, indices].sum(axis=1).astype(np.float32)
    mean_val = np.mean(combined)
    std_val = np.std(combined)
    if std_val > 1e-8:
        return (combined - mean_val) / std_val
    return combined - mean_val


def compute_band_energy_welch(
    signal_1d: np.ndarray,
    fs_hz: float,
    low_hz: float,
    high_hz: float,
) -> float:
    """
    Welch PSD band energy: integrates the power spectral density over [low_hz, high_hz].

    Uses a single segment (nperseg=len(signal_1d), no multi-segment averaging) --
    prioritizes responsiveness over variance reduction.
    """
    n = len(signal_1d)
    if n < 8:
        return 0.0
    freqs, psd = signal.welch(signal_1d, fs=fs_hz, nperseg=n)
    band_mask = (freqs >= low_hz) & (freqs <= high_hz)
    if not np.any(band_mask):
        return 0.0
    return float(_trapezoid(psd[band_mask], freqs[band_mask]))


#: numpy 2.0 이 `np.trapz` 를 제거하고 `np.trapezoid` 로 이름을 바꿨다.
#: requirements-pi.txt 가 numpy 1.26 을 핀하고 개발 PC 는 2.x 라 양쪽을 지원해야 한다.
#: 같은 결함이 ESP 사본에서는 REVIEW P13 으로 고쳐졌는데 이 사본은 누락돼 있었다 —
#: `__main__.py` 가 없어 재실 루프가 한 번도 돌지 않았기 때문에 드러나지 않았다.
_trapezoid = getattr(np, "trapezoid", None) or np.trapz  # type: ignore[attr-defined]


def compute_final_signal(
    timestamps_us: np.ndarray,
    amp_2d: np.ndarray,
    window_sec: float = 3.0,
    stride_sec: float = 0.5,
    fs_hz: float = 100.0,
    omega: int = 25,
    n_streams: int = 10,
    bandpass_low: float = 2.0,
    bandpass_high: float = 50.0,
    bandpass_order: int = 4,
    compute_band_energy: bool = False,
    energy_band_low: float | None = None,
    energy_band_high: float | None = None,
) -> FinalSignalResult | None:
    """
    Complete per-tick pipeline: resample -> bandpass -> select -> sum -> moving variance.

    timestamps_us must be in **microseconds** (see preprocessing.resample_signal) --
    callers pulling from backend.csi.buffer.RingBuffer (which returns seconds) must
    convert with `times * 1e6` before calling this.

    compute_band_energy=True additionally computes a Welch PSD band-energy value
    from final_signal, for the wander (presence) signal. energy_band_low/high should
    normally be a NARROWER band nested inside [bandpass_low, bandpass_high] -- if
    they're the same band, the pre-filter step already confines final_signal's power
    almost entirely to that band before normalization, making the post-normalization
    Welch measurement in the same band close to input-invariant. Pre-filtering wider
    than the measurement band preserves real discriminative power (see
    reference/fall_detect/occupation_pipline.md §2 for the empirical justification).
    """
    n_samples, n_subcarriers = amp_2d.shape
    if n_samples < 10:
        return None

    amp_resampled, resample_stats = resample_signal(timestamps_us, amp_2d, fs_hz=fs_hz)

    amp_filtered, _bp_diagnostics = safe_bandpass(
        amp_resampled, fs_hz, bandpass_low, bandpass_high, bandpass_order
    )

    selected_indices, q_values = select_top_subcarriers_2d(amp_filtered, omega, n_streams)

    final_signal = sum_and_normalize(amp_filtered, selected_indices)

    mv = _moving_variance(final_signal, omega)
    mv_current = float(mv[-1])

    band_energy = None
    if compute_band_energy:
        e_low = energy_band_low if energy_band_low is not None else bandpass_low
        e_high = energy_band_high if energy_band_high is not None else bandpass_high
        band_energy = compute_band_energy_welch(final_signal, fs_hz, e_low, e_high)

    window_duration_s = (timestamps_us[-1] - timestamps_us[0]) / 1e6

    return FinalSignalResult(
        final_signal=final_signal,
        moving_variance=mv,
        mv_current=mv_current,
        selected_indices=selected_indices,
        q_values=q_values,
        resample_stats=resample_stats,
        window_sample_count=n_samples,
        window_duration_s=window_duration_s,
        band_energy=band_energy,
    )
