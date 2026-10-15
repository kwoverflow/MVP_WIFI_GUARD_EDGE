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


def test_gap_counter_and_nonfinite_rejection():
    buffer = AmplitudeBuffer()
    buffer.append(frame(0))
    buffer.append(frame(3))
    assert buffer.sequence_gaps == 2
    broken = frame(4)
    broken.amps[:] = np.nan
    with pytest.raises(ValueError, match="finite"):
        buffer.append(broken)
