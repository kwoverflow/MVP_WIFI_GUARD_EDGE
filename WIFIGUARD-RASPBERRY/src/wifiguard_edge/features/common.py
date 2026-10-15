"""S3 scalogram 피처 계산 코어.

ACF_Scalogram_FeatureExtraction/scripts/amfall_losnlos_common.py 에서
실시간 추론에 필요한 함수만 그대로 이식했다 (수식 변경 금지).
원본과 달라지면 학습 데이터와 피처 분포가 어긋나 모델 성능이 무효가 된다.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def moving_mean(values: np.ndarray, radius: int) -> np.ndarray:
    radius = max(0, int(radius))
    if radius == 0:
        return values.astype(np.float64)
    kernel = np.ones(2 * radius + 1, dtype=np.float64)
    counts = np.convolve(np.ones(len(values), dtype=np.float64), kernel, mode="same")
    sums = np.convolve(values.astype(np.float64), kernel, mode="same")
    return sums / np.maximum(counts, 1.0)


def moving_variance(values: np.ndarray, radius: int) -> np.ndarray:
    values = values.astype(np.float64)
    mean = moving_mean(values, radius)
    mean_sq = moving_mean(values * values, radius)
    return np.maximum(mean_sq - mean * mean, 0.0)


def q_metric(values: np.ndarray, radius: int, eps: float = 1e-8) -> float:
    variance = moving_variance(np.abs(values), radius)
    return float(np.max(variance) / max(float(np.mean(variance)), eps))


def select_streams(amplitude: np.ndarray, omega: int, w_radius: int) -> tuple[np.ndarray, np.ndarray]:
    q_values = np.asarray([q_metric(amplitude[:, idx], w_radius) for idx in range(amplitude.shape[1])])
    order = np.argsort(q_values)[::-1]
    top = order[: min(max(1, omega), len(order))]
    q_top = q_values[top]
    q_sum = max(float(np.sum(q_top)), 1e-8)
    q_norm = q_top / q_sum
    selected_mask = q_norm >= (1.0 / max(1, len(top)))
    selected = top[selected_mask]
    if len(selected) == 0:
        selected = top[:1]
    return selected.astype(np.int32), q_values


def select_pc_signal(
    amplitude: np.ndarray,
    selected_streams: np.ndarray,
    w_radius: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    x = amplitude[:, selected_streams].astype(np.float64)
    x_centered = x - np.mean(x, axis=0, keepdims=True)
    if x_centered.shape[1] == 1:
        pc = x_centered[:, 0]
        return pc.astype(np.float32), {
            "selected_pc_indices": "0",
            "candidate_pc_count": 1,
            "selected_pc_count": 1,
        }

    _, singular_values, vt = np.linalg.svd(x_centered, full_matrices=False)
    denom = max(x_centered.shape[0] - 1, 1)
    eigenvalues = (singular_values * singular_values) / denom
    eigen_sum = max(float(np.sum(eigenvalues)), 1e-12)
    normalized = eigenvalues / eigen_sum
    threshold = 1.0 / x_centered.shape[1]
    candidate_indices = np.where(normalized > threshold)[0]
    if len(candidate_indices) == 0:
        candidate_indices = np.asarray([int(np.argmax(normalized))])
    pcs = x_centered @ vt.T

    if len(candidate_indices) == 1:
        selected_pc_indices = candidate_indices
    else:
        q_values = np.asarray([q_metric(pcs[:, idx], w_radius) for idx in candidate_indices])
        q_norm = q_values / max(float(np.sum(q_values)), 1e-8)
        pc_threshold = 1.0 / max(len(candidate_indices) - 1, 1)
        selected_pc_indices = candidate_indices[q_norm >= pc_threshold]
        if len(selected_pc_indices) == 0:
            selected_pc_indices = np.asarray([candidate_indices[int(np.argmax(q_values))]])

    signal = np.sum(pcs[:, selected_pc_indices], axis=1)
    return signal.astype(np.float32), {
        "selected_pc_indices": ";".join(str(int(idx)) for idx in selected_pc_indices),
        "candidate_pc_count": int(len(candidate_indices)),
        "selected_pc_count": int(len(selected_pc_indices)),
    }


def resize_time_axis(matrix: np.ndarray, output_time_bins: int) -> np.ndarray:
    if matrix.shape[1] == output_time_bins:
        return matrix.astype(np.float32)
    old_x = np.linspace(0.0, 1.0, matrix.shape[1], dtype=np.float64)
    new_x = np.linspace(0.0, 1.0, output_time_bins, dtype=np.float64)
    resized = np.empty((matrix.shape[0], output_time_bins), dtype=np.float32)
    for row_idx in range(matrix.shape[0]):
        resized[row_idx] = np.interp(new_x, old_x, matrix[row_idx]).astype(np.float32)
    return resized


def general_denoise(s0: np.ndarray, signal_q: float, th_scmax: float) -> np.ndarray:
    if signal_q <= 1.0 or not math.isfinite(signal_q):
        th_sc = th_scmax
    else:
        log_q = math.log10(signal_q)
        th_sc = th_scmax if log_q <= 0 else min(1.0 / log_q, th_scmax)
    s1 = s0.copy()
    row_means = np.mean(s1, axis=1)
    thresholds = row_means * th_sc
    s1[s1 < thresholds[:, None]] = 0.0
    return s1


def vertical_denoise(s1: np.ndarray, freqs: np.ndarray, d_hz_per_step: float) -> np.ndarray:
    s2 = s1.copy()
    descending = np.argsort(freqs)[::-1]
    fp_max = 0.0
    for col_idx in range(s2.shape[1]):
        for row_idx in descending:
            if s2[row_idx, col_idx] <= 0:
                continue
            freq = float(freqs[row_idx])
            if fp_max <= 0.0:
                fp_max = freq
                break
            if freq <= fp_max or (freq - fp_max) <= d_hz_per_step:
                fp_max = max(fp_max, freq)
                break
            s2[row_idx, col_idx] = 0.0
    return s2


def horizontal_denoise(
    s2: np.ndarray,
    freqs: np.ndarray,
    fs_hz: float,
    carrier_hz: float,
    kappa: float,
) -> np.ndarray:
    s3 = s2.copy()
    c = 299_792_458.0
    wavelength = c / carrier_hz
    g = 9.80665
    ts = 1.0 / fs_hz
    if len(freqs) == 1:
        bandwidths = np.ones_like(freqs)
    else:
        bandwidths = np.gradient(freqs)
        bandwidths = np.maximum(np.abs(bandwidths), np.min(np.abs(bandwidths[np.nonzero(bandwidths)])))
    min_runs = np.maximum(
        1,
        np.ceil((bandwidths * wavelength) / max(kappa * g * ts, 1e-12)).astype(int),
    )
    for row_idx in range(s3.shape[0]):
        min_run = int(min_runs[row_idx])
        nonzero = s3[row_idx] > 0
        idx = 0
        while idx < len(nonzero):
            if not nonzero[idx]:
                idx += 1
                continue
            start = idx
            while idx < len(nonzero) and nonzero[idx]:
                idx += 1
            if idx - start < min_run:
                s3[row_idx, start:idx] = 0.0
    return s3


def fallback_cwt(signal: np.ndarray, freqs: np.ndarray, fs_hz: float) -> np.ndarray:
    """ssqueezepy가 없을 때 쓰는 NumPy 전용 CWT (원본 대비 근사)."""
    centered = signal - float(np.mean(signal))
    outputs = np.empty((len(freqs), len(centered)), dtype=np.complex64)
    for row_idx, freq in enumerate(freqs):
        cycles = 6.0
        sigma_sec = cycles / max(2.0 * math.pi * float(freq), 1e-6)
        radius = int(min(max(8, math.ceil(3.0 * sigma_sec * fs_hz)), max(8, len(centered) // 2)))
        t = np.arange(-radius, radius + 1, dtype=np.float64) / fs_hz
        envelope = np.exp(-0.5 * np.square(t / max(sigma_sec, 1e-6)))
        carrier = np.exp(2j * math.pi * float(freq) * t)
        wavelet = envelope * carrier
        norm = np.sqrt(np.sum(np.square(np.abs(wavelet))))
        if norm > 0:
            wavelet = wavelet / norm
        convolved = np.convolve(centered, np.conj(wavelet[::-1]), mode="same")
        if len(convolved) != len(centered):
            start = max(0, (len(convolved) - len(centered)) // 2)
            convolved = convolved[start : start + len(centered)]
        outputs[row_idx] = convolved.astype(np.complex64)
    return outputs


# (wavelet_name, N, fs, freq_min, freq_max_eff, image_size) -> (Wavelet, scales)
# freq_to_scale이 호출당 약 0.5초로 파이프라인 병목인데, 입력이 같으면 결과가
# 결정적이므로 캐시한다. 수신률(fs)은 장치 타임스탬프 중앙값에서 나와 소수의
# 이산값만 가지므로 적중률이 높다. 캐시 적중 시 결과는 비트 단위로 동일하다.
_SCALE_CACHE: dict[tuple, tuple[Any, np.ndarray]] = {}
_SCALE_CACHE_MAX = 64


def _cached_wavelet_scales(
    wavelet_name: str,
    n_signal: int,
    fs_hz: float,
    freqs_low_to_high: np.ndarray,
    effective_freq_max: float,
    image_size: int,
) -> tuple[Any, np.ndarray]:
    from ssqueezepy.experimental import freq_to_scale
    from ssqueezepy.wavelets import Wavelet

    key = (wavelet_name, n_signal, float(fs_hz), float(freqs_low_to_high[0]), float(effective_freq_max), image_size)
    cached = _SCALE_CACHE.get(key)
    if cached is not None:
        return cached
    wavelet = Wavelet(wavelet_name, N=n_signal)
    scales = freq_to_scale(freqs_low_to_high, wavelet, N=n_signal, fs=fs_hz)[::-1]
    if len(_SCALE_CACHE) >= _SCALE_CACHE_MAX:
        _SCALE_CACHE.pop(next(iter(_SCALE_CACHE)))
    _SCALE_CACHE[key] = (wavelet, scales)
    return wavelet, scales


def compute_s3_scalogram(
    signal: np.ndarray,
    fs_hz: float,
    freq_min_hz: float,
    freq_max_hz: float,
    image_size: int,
    th_scmax: float,
    kappa: float,
    carrier_hz: float,
    wavelet_name: str = "gmw",
) -> tuple[np.ndarray, dict[str, Any]]:
    """원본 compute_scalogram_stages와 동일한 계산에서 s3만 반환한다."""
    effective_freq_max = min(freq_max_hz, fs_hz / 2.0 - 1e-6)
    freqs_low_to_high = np.linspace(freq_min_hz, effective_freq_max, image_size, dtype=np.float64)
    freqs = freqs_low_to_high[::-1]
    try:
        from ssqueezepy import cwt

        wavelet, scales = _cached_wavelet_scales(
            wavelet_name, len(signal), fs_hz, freqs_low_to_high, effective_freq_max, image_size
        )
        wx, _ = cwt(
            signal.astype(np.float64),
            wavelet=wavelet,
            scales=scales,
            fs=fs_hz,
            l1_norm=True,
            astensor=False,
        )
    except ModuleNotFoundError:
        wx = fallback_cwt(signal=signal.astype(np.float64), freqs=freqs, fs_hz=fs_hz)
    s0 = np.abs(wx).astype(np.float32)
    max_value = float(np.max(s0))
    if max_value > 0:
        s0 /= max_value
    signal_q = q_metric(signal, max(1, int(round(0.4 * fs_hz))))
    s1 = general_denoise(s0, signal_q, th_scmax=th_scmax)
    d_hz_per_step = 170.0 / fs_hz
    s2 = vertical_denoise(s1, freqs, d_hz_per_step=d_hz_per_step)
    s3 = horizontal_denoise(s2, freqs, fs_hz=fs_hz, carrier_hz=carrier_hz, kappa=kappa)
    stats = {
        "signal_q": signal_q,
        "effective_freq_max_hz": float(effective_freq_max),
        "s3_nonzero_ratio": float(np.mean(s3 > 0)),
    }
    return resize_time_axis(s3, image_size).astype(np.float32), stats
