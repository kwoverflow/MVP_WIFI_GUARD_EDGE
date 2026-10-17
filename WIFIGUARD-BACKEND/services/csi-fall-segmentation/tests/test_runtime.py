from __future__ import annotations

import numpy as np

from csi_fall_segmentation.engine import center_probabilities
from csi_fall_segmentation.online import MODEL_WINDOW_SAMPLES, resample_amplitude
from csi_fall_segmentation.postprocess import ABTriggerState


def test_320_hz_window_resamples_to_training_shape():
    values = np.arange(960 * 30, dtype=np.float32).reshape(960, 30)
    output = resample_amplitude(values, 320.0)
    assert output.shape == (MODEL_WINDOW_SAMPLES, 30)
    assert np.isfinite(output).all()


def test_center_probability_is_interpolated():
    values = np.tile(np.linspace(0, 1, 64, dtype=np.float32), (2, 1))
    result = center_probabilities(values, 500)
    assert result.shape == (2,)
    assert np.allclose(result, 0.5)


def test_b_trigger_is_causal_four_of_four():
    state = ABTriggerState()
    outputs = [state.update(value)["b_trigger"] for value in (0.3, 0.3, 0.3, 0.3)]
    assert outputs[-1] == 1
