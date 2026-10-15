from types import SimpleNamespace

import numpy as np
import pytest

from csi_fall_segmentation.features import q_metric
from wifiguard_local_edge.buffer import AmplitudeBuffer, prepare_window
from wifiguard_local_edge.carriers import ModelWindowPreparer, q_scores, select_q_top30


def test_vectorized_q_matches_scalar_formula_and_exact_ranking():
    values = np.random.default_rng(41).uniform(3, 50, (960, 245)).astype(np.float32)
    actual = q_scores(values, 128)
    expected = np.asarray([q_metric(values[:, i], 128) for i in range(245)])
    np.testing.assert_allclose(actual, expected, rtol=1e-8, atol=1e-8)
    candidates = np.array([i for i in range(245) if i not in (121, 122)])
    selected, scores = select_q_top30(values)
    np.testing.assert_array_equal(selected, candidates[np.lexsort((candidates, -expected[candidates]))][:30])
    np.testing.assert_allclose(scores, expected[selected], rtol=1e-8, atol=1e-8)
    assert len(set(selected)) == 30 and 121 not in selected and 122 not in selected


def test_ties_are_deterministic_and_never_select_excluded_carriers():
    selected, scores = select_q_top30(np.full((960, 245), 23.3, dtype=np.float32))
    np.testing.assert_array_equal(selected, np.arange(30))
    np.testing.assert_array_equal(scores, np.zeros(30))


def test_selection_uses_full_window_and_keeps_channel_identity():
    times = np.arange(967) / 320.0
    values = np.random.default_rng(44).uniform(1, 80, (967, 245)).astype(np.float32)
    preparer = ModelWindowPreparer("q-top30")
    actual = preparer.prepare(times, values)
    uniform = prepare_window(times, values, expected_channels=245)
    selected, _ = select_q_top30(uniform)
    np.testing.assert_array_equal(actual, uniform[:, selected])
    assert actual.shape == (960, 30)
    assert preparer.last_selection["checkpoint_input_equivalent"] is False


def test_fixed_path_is_unchanged():
    values = np.random.default_rng(4).normal(size=(967, 30)).astype(np.float32)
    times = np.arange(967) / 320
    np.testing.assert_array_equal(ModelWindowPreparer().prepare(times, values), prepare_window(times, values))


@pytest.mark.parametrize("defect", ["nan", "gap", "backward", "short", "missing_carriers"])
def test_invalid_raw_never_reaches_q_ranking(defect):
    times = np.arange(967) / 320
    values = np.ones((967, 245), dtype=np.float32)
    if defect == "nan":
        values[100, 3] = np.nan
    elif defect == "gap":
        times[300:] += .03
    elif defect == "backward":
        times[300] = times[299]
    elif defect == "short":
        times, values = times[:500], values[:500]
    else:
        values = values[:, :30]
    with pytest.raises(ValueError):
        ModelWindowPreparer("q-top30").prepare(times, values)


def test_q_buffer_mode_does_not_discard_raw_carriers():
    ring = AmplitudeBuffer(keep_all=True)
    ring.append(SimpleNamespace(amps=np.arange(245, dtype=np.float32), seq=0,
                               timestamp_us=0, host_time=0, rssi=-40))
    assert ring.get_model_window(3, "q-top30")[1].shape == (1, 245)
    assert ring.get_model_window(3)[1].shape == (1, 30)
    with pytest.raises(ValueError, match="full RAW"):
        AmplitudeBuffer().get_model_window(3, "q-top30")
