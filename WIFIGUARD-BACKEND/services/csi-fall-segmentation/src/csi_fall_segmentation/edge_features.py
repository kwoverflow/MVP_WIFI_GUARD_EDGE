"""CPU feature path: original wavelet/PCA, compiled denoising, batch size one.

The reference implementation stays available as the numerical oracle. No
fastmath, reduced scales, quantization, or learned-input changes are used here.
"""

from __future__ import annotations

import time

import numpy as np
from numba import njit

from .features import (
    DEFAULT_CONFIG,
    _wavelet_and_scales,
    compute_acf,
    general_denoise,
    q_metric,
    resize_time_axis,
    select_pc_signal,
    select_streams,
)
from .online import MODEL_FS_HZ, SegmentationFeatures, resample_amplitude


@njit(cache=True, nogil=True)
def vertical_kernel(values, frequencies, descending, hz_step):
    output = values.copy()
    maximum = 0.0
    for column in range(output.shape[1]):
        for row in descending:
            if output[row, column] <= 0:
                continue
            frequency = float(frequencies[row])
            if maximum <= 0.0:
                maximum = frequency
                break
            if frequency <= maximum or frequency - maximum <= hz_step:
                maximum = max(maximum, frequency)
                break
            output[row, column] = 0.0
    return output


@njit(cache=True, nogil=True)
def horizontal_kernel(values, minimum_runs):
    output = values.copy()
    for row in range(output.shape[0]):
        index = 0
        while index < output.shape[1]:
            if output[row, index] <= 0:
                index += 1
                continue
            start = index
            while index < output.shape[1] and output[row, index] > 0:
                index += 1
            if index - start < minimum_runs[row]:
                output[row, start:index] = 0.0
    return output


class CpuEdgeFeatureBuilder:
    """Reuse scale/run arrays, pay JIT setup at startup, record actual stages."""

    def __init__(self):
        self.config = dict(DEFAULT_CONFIG)
        self.wavelet, self.scales, self.freqs = _wavelet_and_scales(
            500,
            MODEL_FS_HZ,
            self.config["freq_min_hz"],
            self.config["freq_max_hz"],
            self.config["image_size"],
        )
        self.descending = np.argsort(self.freqs)[::-1].copy()
        bandwidths = np.gradient(self.freqs)
        bandwidths = np.maximum(
            np.abs(bandwidths),
            np.min(np.abs(bandwidths[np.nonzero(bandwidths)])),
        )
        self.minimum_runs = np.maximum(
            1,
            np.ceil(
                bandwidths
                * (299_792_458.0 / self.config["carrier_hz"])
                / max(self.config["kappa"] * 9.80665 / MODEL_FS_HZ, 1e-12),
            ).astype(np.int64),
        )
        self.last_timings_ms: dict[str, float] = {}

    def build_native(self, window: np.ndarray, fs_hz: float = MODEL_FS_HZ):
        """Native 500x30 path, also used for supplier parity checks."""
        from ssqueezepy import cwt

        if fs_hz != MODEL_FS_HZ or window.shape != (500, 30):
            raise ValueError("CPU edge native input must be 500x30 at 500/3 Hz")
        started = time.perf_counter()
        radius = max(1, int(round(0.4 * fs_hz)))
        selected = select_streams(window, int(self.config["omega"]), radius)
        signal = select_pc_signal(window, selected, radius)
        after_pca = time.perf_counter()
        coefficients, _ = cwt(
            signal[None].astype(np.float64),
            wavelet=self.wavelet,
            scales=self.scales,
            fs=fs_hz,
            l1_norm=True,
            astensor=False,
            vectorized=True,
            cache_wavelet=True,
        )
        s0 = np.abs(coefficients[0]).astype(np.float32)
        maximum = float(np.max(s0))
        if maximum > 0:
            s0 /= maximum
        after_cwt = time.perf_counter()
        s1 = general_denoise(s0, q_metric(signal, radius), self.config["th_scmax"])
        s2 = vertical_kernel(s1, self.freqs, self.descending, 170.0 / fs_hz)
        s3 = horizontal_kernel(s2, self.minimum_runs)
        s3 = resize_time_axis(s3, 224)
        after_denoise = time.perf_counter()
        acf = compute_acf(signal, fs_hz, self.config)
        finished = time.perf_counter()
        self.last_timings_ms = {
            "q_pca": (after_pca - started) * 1000,
            "cwt": (after_cwt - after_pca) * 1000,
            "denoise_resize": (after_denoise - after_cwt) * 1000,
            "acf": (finished - after_denoise) * 1000,
        }
        return SegmentationFeatures(s3=s3[None], acf=acf[None])

    def build(self, amplitude: np.ndarray, fs_hz: float):
        started = time.perf_counter()
        native = resample_amplitude(amplitude, fs_hz)
        elapsed = (time.perf_counter() - started) * 1000
        result = self.build_native(native)
        self.last_timings_ms["resample"] = elapsed
        return result

    def warmup(self):
        values = np.tile(
            np.linspace(19.5, 20.5, 960, dtype=np.float32)[:, None], (1, 30)
        )
        self.build(values, 320.0)
