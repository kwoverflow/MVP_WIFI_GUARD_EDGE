"""Adapter from the edge 320 Hz amplitude contract to the model's native cadence."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly

from .features import DEFAULT_CONFIG, compute_feature_pairs

MODEL_FS_HZ = 500.0 / 3.0
MODEL_WINDOW_SAMPLES = 500
MODEL_SUBCARRIERS = 30


class IncompatibleSignalError(ValueError):
    """Signal is valid on the wire but cannot feed this checkpoint."""


@dataclass(frozen=True)
class SegmentationFeatures:
    s3: np.ndarray
    acf: np.ndarray


def resample_amplitude(amplitude: np.ndarray, source_fs_hz: float) -> np.ndarray:
    values = np.asarray(amplitude, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != MODEL_SUBCARRIERS:
        raise IncompatibleSignalError(
            f"segmentation checkpoint requires (time, 30), got {values.shape}"
        )
    if source_fs_hz <= 0:
        raise IncompatibleSignalError("source sample rate must be positive")
    if not 2.8 <= len(values) / float(source_fs_hz) <= 3.2:
        raise IncompatibleSignalError(
            f"segmentation checkpoint requires a 3 second window, got "
            f"{len(values) / float(source_fs_hz):.3f}s"
        )
    ratio = Fraction(MODEL_FS_HZ / float(source_fs_hz)).limit_denominator(4096)
    output = resample_poly(values, ratio.numerator, ratio.denominator, axis=0)
    if len(output) < MODEL_WINDOW_SAMPLES:
        output = np.pad(output, ((0, MODEL_WINDOW_SAMPLES - len(output)), (0, 0)), mode="edge")
    elif len(output) > MODEL_WINDOW_SAMPLES:
        output = output[:MODEL_WINDOW_SAMPLES]
    return np.ascontiguousarray(output, dtype=np.float32)


class SegmentationFeatureBuilder:
    def start(self) -> None:
        if int(DEFAULT_CONFIG["target_subcarriers"]) != MODEL_SUBCARRIERS:
            raise RuntimeError("unexpected segmentation feature contract")

    def warmup(self, batch_size: int = 4, fs_hz: float = 320.0) -> None:
        """Pay ssqueezepy/CUDA kernel setup before consuming Kafka offsets."""
        baseline = np.tile(np.linspace(19.5, 20.5, 960, dtype=np.float32)[:, None], (1, 30))
        self.build_batch([baseline] * max(1, batch_size), fs_hz)

    def build_batch(
        self, amplitudes: list[np.ndarray], fs_hz: float
    ) -> SegmentationFeatures:
        if not amplitudes:
            raise ValueError("empty inference batch")
        windows = np.stack([resample_amplitude(value, fs_hz) for value in amplitudes])
        s3, acf = compute_feature_pairs(windows, MODEL_FS_HZ, DEFAULT_CONFIG)
        return SegmentationFeatures(
            s3=s3,
            acf=acf,
        )

    def build(self, amplitude: np.ndarray, fs_hz: float) -> SegmentationFeatures:
        return self.build_batch([amplitude], fs_hz)
