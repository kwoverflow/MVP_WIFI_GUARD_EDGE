import os

os.environ["SSQ_GPU"] = "0"
os.environ["SSQ_PARALLEL"] = "0"

import numpy as np
import pytest

from csi_fall_segmentation.edge_features import (
    CpuEdgeFeatureBuilder,
    horizontal_kernel,
    vertical_kernel,
)
from csi_fall_segmentation.features import (
    DEFAULT_CONFIG,
    horizontal_denoise,
    vertical_denoise,
)
from csi_fall_segmentation.online import MODEL_FS_HZ, SegmentationFeatureBuilder


@pytest.mark.parametrize("seed", [0, 4, 23])
def test_compiled_denoising_is_exact(seed):
    rng = np.random.default_rng(seed)
    values = rng.random((224, 500), dtype=np.float32)
    values[values < 0.8] = 0
    builder = CpuEdgeFeatureBuilder()
    expected = vertical_denoise(values, builder.freqs, 170 / MODEL_FS_HZ)
    actual = vertical_kernel(
        values, builder.freqs, builder.descending, 170 / MODEL_FS_HZ
    )
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(
        horizontal_kernel(actual, builder.minimum_runs),
        horizontal_denoise(
            expected,
            builder.freqs,
            MODEL_FS_HZ,
            DEFAULT_CONFIG["carrier_hz"],
            DEFAULT_CONFIG["kappa"],
        ),
    )


@pytest.mark.parametrize("kind", ["quiet", "noise", "burst"])
def test_full_features_match_reference(kind):
    rng = np.random.default_rng(7)
    window = np.full((960, 30), 20, dtype=np.float32)
    if kind == "noise":
        window += rng.normal(0, 0.1, window.shape).astype(np.float32)
    if kind == "burst":
        t = np.arange(960) / 320
        signal = np.sin(2 * np.pi * 16 * t) * np.exp(-(((t - 1.5) / 0.2) ** 2))
        window += (
            signal[:, None].astype(np.float32)
            * np.linspace(0.1, 2, 30, dtype=np.float32)[None]
        )
    before = SegmentationFeatureBuilder().build(window, 320)
    after = CpuEdgeFeatureBuilder().build(window, 320)
    np.testing.assert_allclose(after.s3, before.s3, atol=3e-4, rtol=0)
    np.testing.assert_allclose(after.acf, before.acf, atol=3e-4, rtol=0)
