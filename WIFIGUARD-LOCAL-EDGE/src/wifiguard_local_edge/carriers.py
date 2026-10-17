"""Explicit model-input experiment, not an equivalent checkpoint optimization.

Rank valid RAW carriers on the latest uniform 3-second / 320-Hz amplitude
window. Q uses the model's truncated moving-variance definition and 0.4-second
radius. Keep the training DC exclusions, break ties by RAW index, retain exactly
30 carriers in descending Q order. Downstream resampling/Q/PCA/A/B is unchanged.
"""

from __future__ import annotations

import time

import numpy as np
from scipy.ndimage import uniform_filter1d

from wifiguard_edge.features.realtime import FeatureConfig, select_subcarrier_indices

from .buffer import prepare_window


def q_scores(amplitude, radius):
    """Vectorized truncated box moments; same formula as features.q_metric.

    Blocks bound temporary memory. No channel reduction or approximate Q metric.
    Floating summation order differs from the scalar convolution oracle.
    """
    values = np.asarray(amplitude, dtype=np.float64)
    if values.ndim != 2 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError("Q ranking requires a finite time-by-carrier matrix")
    if not 0 < radius < len(values):
        raise ValueError("Q radius must be smaller than the window")
    size = 2 * radius + 1
    positions = np.arange(len(values))
    counts = (np.minimum(len(values), positions + radius + 1)
              - np.maximum(0, positions - radius))[:, None]
    scores = np.empty(values.shape[1], dtype=np.float64)
    for start in range(0, values.shape[1], 32):
        block = np.abs(values[:, start:start + 32])
        mean = uniform_filter1d(block, size=size, axis=0, mode="constant") * size / counts
        second = uniform_filter1d(block * block, size=size, axis=0, mode="constant") * size / counts
        variance = np.maximum(second - mean * mean, 0.0)
        # A truly constant carrier has no motion, including non-integer levels.
        variance[:, np.ptp(block, axis=0) == 0] = 0
        scores[start:start + block.shape[1]] = (
            np.max(variance, axis=0) / np.maximum(np.mean(variance, axis=0), 1e-8)
        )
    return scores


def select_q_top30(amplitude, fs_hz=320.0):
    values = np.asarray(amplitude)
    cfg = FeatureConfig()
    if values.ndim != 2 or values.shape[1] != cfg.train_raw_pairs:
        raise ValueError("q-top30 requires all 245 RAW carriers")
    candidates = np.asarray([
        i for i in range(cfg.train_raw_pairs) if i not in cfg.train_drop_pair_indices
    ], dtype=np.int32)
    radius = max(1, round(cfg.moving_variance_radius_seconds * fs_hz))
    scores = q_scores(values, radius)
    order = np.lexsort((candidates, -scores[candidates]))
    selected = candidates[order[:cfg.target_subcarriers]]
    return selected, scores[selected]


class ModelWindowPreparer:
    def __init__(self, mode="training-fixed"):
        if mode not in ("training-fixed", "q-top30"):
            raise ValueError("unsupported model carrier selection")
        self.mode = mode
        self.last_selection = {"mode": mode, "checkpoint_input_equivalent": mode == "training-fixed"}

    def prepare(self, times, amplitude):
        started = time.perf_counter()
        if self.mode == "training-fixed":
            selected = select_subcarrier_indices(245, FeatureConfig())
            values = amplitude[:, selected] if amplitude.shape[1] == 245 else amplitude
            output = prepare_window(times, values)
            scores = None
        else:
            # Interpolate once before Q ranking; do not rank irregular or stale
            # data, and do not select different carriers for individual frames.
            uniform = prepare_window(times, amplitude, expected_channels=245)
            selected, scores = select_q_top30(uniform)
            output = np.ascontiguousarray(uniform[:, selected], dtype=np.float32)
        self.last_selection = {
            "mode": self.mode,
            "checkpoint_input_equivalent": self.mode == "training-fixed",
            "raw_pair_indices": selected.tolist(),
            "q_values": scores.tolist() if scores is not None else None,
            "q_source_fs_hz": 320.0 if scores is not None else None,
            "q_radius_seconds": 0.4 if scores is not None else None,
            "processing_ms": (time.perf_counter() - started) * 1000,
        }
        return output
