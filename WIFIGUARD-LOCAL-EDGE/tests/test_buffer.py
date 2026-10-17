from types import SimpleNamespace

import numpy as np
import pytest

from wifiguard_local_edge.buffer import AmplitudeBuffer, prepare_window
from wifiguard_edge.features.realtime import FeatureConfig, select_subcarrier_indices


def frame(seq, timestamp=None):
    return SimpleNamespace(
        seq=seq,
        timestamp_us=round(seq * 1e6 / 320) if timestamp is None else timestamp,
        amps=np.arange(245, dtype=np.float32),
        host_time=1.0,
        rssi=-40,
    )


def test_bounded_ring_selects_original_carriers_and_latest_window():
    buffer = AmplitudeBuffer()
    for i in range(3000):
        buffer.append(frame(i))
    times, amps = buffer.get_window(3.02)
    assert len(times) < 1000
    np.testing.assert_array_equal(
        amps[0], select_subcarrier_indices(245, FeatureConfig())
    )
    assert buffer.stats()["buffered_frames"] == buffer.capacity
    assert buffer.stats()["array_bytes"] < 400_000
    assert prepare_window(times, amps).shape == (960, 30)


def test_reset_and_clock_wrap_do_not_mix_epochs():
    buffer = AmplitudeBuffer()
    buffer.append(frame(1, (1 << 32) - 3000))
    buffer.append(frame(2, 125))
    times, _ = buffer.get_window(1)
    assert times[-1] > times[0]
    assert buffer.device_resets == 0
    buffer.append(frame(0))
    assert buffer.stats()["buffered_frames"] == 1
    assert buffer.device_resets == 1


def test_outage_is_not_interpolated_into_model_input():
    times = np.arange(960) / 320
    times[500:] += 0.1
    with pytest.raises(ValueError, match="gap"):
        prepare_window(times, np.ones((960, 30)))


def test_latest_grid_matches_original_regular_input():
    times = np.arange(960) / 320
    amps = np.random.default_rng(8).random((960, 30), dtype=np.float32)
    np.testing.assert_array_equal(prepare_window(times, amps), amps)


@pytest.mark.parametrize('width', [30, 245])
@pytest.mark.parametrize('kind', ['regular', 'jitter', 'holes', 'wrap', 'strided'])
def test_gil_free_interpolation_is_bitwise_equal_to_numpy_oracle(width, kind):
    rng = np.random.default_rng(803)
    times = np.arange(1000) / 320
    amps = rng.uniform(-100, 100, (1000, width)).astype(np.float32)
    if kind == 'jitter':
        times[1:] += rng.uniform(-.0002, .0002, len(times) - 1)
    elif kind == 'holes':
        keep = np.ones(1000, dtype=bool)
        keep[[34, 140, 392, 393, 781]] = False
        times, amps = times[keep], amps[keep]
    elif kind == 'wrap':
        times += (1 << 32) / 1e6
    elif kind == 'strided':
        amps = np.asfortranarray(amps)[:, ::-1]
    grid = times[0] + np.arange(int(np.floor((times[-1] - times[0]) * 320 + 1e-7)) + 1) / 320
    grid = grid[-960:]
    oracle = np.stack([np.interp(grid, times, amps[:, i]) for i in range(width)], axis=1).astype(np.float32)
    np.testing.assert_array_equal(prepare_window(times, amps, expected_channels=width), oracle)


def test_interpolation_keeps_numpy_endpoint_and_exact_knot_behavior():
    from wifiguard_local_edge.interpolation import interpolate_carriers
    times = np.asarray([0., .3, 1.])
    values = np.asarray([[1., 12.], [-3., 8.], [6., 7.]], dtype=np.float32)
    grid = np.asarray([-.1, 0., .3, .4, 1., 1.00000000001])
    expected = np.stack([np.interp(grid, times, values[:, i]) for i in range(2)], axis=1).astype(np.float32)
    np.testing.assert_array_equal(interpolate_carriers(times, values, grid), expected)


def test_gap_counter_and_nonfinite_rejection():
    buffer = AmplitudeBuffer()
    buffer.append(frame(0))
    buffer.append(frame(3))
    assert buffer.sequence_gaps == 2
    broken = frame(4)
    broken.amps[:] = np.nan
    with pytest.raises(ValueError, match="finite"):
        buffer.append(broken)


def test_clean_window_waits_after_gap_without_weakening_prepare_quality():
    import json
    buffer = AmplitudeBuffer(seconds=12,keep_all=True)
    for i in range(1000):
        buffer.append(frame(i))
    assert buffer.model_window_status()['ready']
    assert json.loads(json.dumps(buffer.model_window_status()))['ready'] is True
    buffer.append(frame(1020))
    state = buffer.model_window_status()
    assert not state['ready'] and state['gap_events'] == 1
    assert state['reason'] == 'recovering_after_gap'
    with pytest.raises(ValueError,match='gap'):
        prepare_window(*buffer.get_model_window(3.02,'q-top30'),expected_channels=245)
    for i in range(1021,1990):
        buffer.append(frame(i))
    assert buffer.model_window_status()['ready']
    assert prepare_window(*buffer.get_model_window(3.02,'q-top30'),expected_channels=245).shape == (960,245)


@pytest.mark.parametrize('count',[1,20,480,481,1200])
def test_fast_ring_matches_original_timestamp_mask_and_owns_snapshots(count):
    buffer = AmplitudeBuffer(seconds=1,keep_all=True)
    for i in range(count):
        buffer.append(frame(i))
    indices=(np.arange(buffer._count)+buffer._head-buffer._count)%buffer.capacity
    for seconds in (0.,.03,.4,1.,10.):
        times=buffer.times[indices]
        expected=indices[times>=times[-1]-seconds]
        actual=buffer.get_window(seconds)
        np.testing.assert_array_equal(actual[0],buffer.times[expected])
        np.testing.assert_array_equal(actual[1],buffer.amps[expected])
        assert not np.shares_memory(actual[1],buffer.amps)
    assert buffer.stats()['hz_1s'] == (float(np.sum(times>times[-1]-1.)) if count>1 else 0.)
