"""PCA-ACF 피처 계산.

ACF_Scalogram_FeatureExtraction/scripts/build_losnlos_pca_motion_acf_dataset.py 와
build_inhouse_native_hz_midpoint_s3_acf.py 에서 모델 입력(pca_acf_lag0p4s)에
필요한 경로만 이식했다. dacf 계열은 모델이 쓰지 않으므로 제외.
"""

from __future__ import annotations

import numpy as np

from .common import resize_time_axis


def normalize_signal_zscore(signal: np.ndarray) -> np.ndarray:
    values = signal.astype(np.float32, copy=False)
    center = float(np.mean(values))
    scale = float(np.std(values))
    if not np.isfinite(scale) or scale < 1e-6:
        scale = 1.0
    return ((values - center) / scale).astype(np.float32)


def percentile_scale(values: np.ndarray, percentile: float) -> float:
    finite = np.asarray(values[np.isfinite(values)], dtype=np.float32)
    if finite.size == 0:
        return 1.0
    scale = float(np.percentile(np.abs(finite), float(percentile)))
    if not np.isfinite(scale) or scale < 1e-6:
        scale = float(np.max(np.abs(finite))) if finite.size else 1.0
    if not np.isfinite(scale) or scale < 1e-6:
        return 1.0
    return scale


def normalize_signed_map(values: np.ndarray, clip_percentile: float) -> np.ndarray:
    arr = values.astype(np.float32, copy=False)
    scale = percentile_scale(arr, clip_percentile)
    return np.clip(arr / scale, -1.0, 1.0).astype(np.float32)


def compute_lag_product_map(signal: np.ndarray, lag_steps: int) -> np.ndarray:
    if lag_steps <= 0:
        raise ValueError(f"lag_steps must be positive, got {lag_steps}")
    if len(signal) <= lag_steps:
        raise ValueError(f"signal length {len(signal)} must exceed lag_steps={lag_steps}")
    current = signal[lag_steps:]
    acf = np.empty((lag_steps, len(signal) - lag_steps), dtype=np.float32)
    for lag in range(1, lag_steps + 1):
        past = signal[lag_steps - lag : len(signal) - lag]
        acf[lag - 1] = current * past
    return acf


def resize_lag_axis(values: np.ndarray, output_bins: int) -> np.ndarray:
    if values.ndim != 3 or values.shape[0] != 1:
        raise ValueError(f"Expected ACF shape (1,lag,time), got {values.shape}")
    if values.shape[1] == output_bins:
        return values.astype(np.float32, copy=True)
    source = np.linspace(0.0, 1.0, values.shape[1], dtype=np.float64)
    target = np.linspace(0.0, 1.0, output_bins, dtype=np.float64)
    output = np.empty((1, output_bins, values.shape[2]), dtype=np.float32)
    for time_index in range(values.shape[2]):
        output[0, :, time_index] = np.interp(target, source, values[0, :, time_index]).astype(np.float32)
    return output


def compute_pca_acf(
    signal: np.ndarray,
    fs_hz: float,
    lag_seconds: float,
    time_bins: int,
    lag_output_bins: int,
    clip_percentile: float,
) -> np.ndarray:
    """PCA motion signal에서 모델 입력 ACF (1, lag_output_bins, time_bins)를 만든다."""
    normalized_signal = normalize_signal_zscore(signal)
    lag_steps = max(1, int(round(lag_seconds * float(fs_hz))))
    acf_raw = compute_lag_product_map(normalized_signal, lag_steps)
    acf_resized = resize_time_axis(acf_raw, int(time_bins))
    acf_norm = normalize_signed_map(acf_resized, clip_percentile)
    return resize_lag_axis(acf_norm[None, :, :].astype(np.float32), int(lag_output_bins))
