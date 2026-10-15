import time

import pytest

from wifiguard_local_edge.buffer import AmplitudeBuffer, prepare_window
from wifiguard_local_edge.replay import LocalReplaySource, RawWgspReplaySource


def test_real_time_replay_uses_stable_device_clock():
    buffer = AmplitudeBuffer()
    source = LocalReplaySource(buffer)
    source.start()
    try:
        # Windows host wakeups can be grouped. Device timestamps must still be
        # stable 320 Hz, with a complete 3-second input for the model.
        deadline = time.monotonic() + 5
        while buffer.total_frames < 980 and time.monotonic() < deadline:
            time.sleep(0.025)
        assert buffer.total_frames >= 980
        times, amps = buffer.get_window(3.02)
        assert prepare_window(times, amps).shape == (960, 30)
        assert source.error is None
    finally:
        source.stop()
        source.join(1)


def test_missing_recording_fails_before_thread_start():
    with pytest.raises(FileNotFoundError):
        LocalReplaySource(AmplitudeBuffer(), "missing-recording.npz")


def test_raw_replay_uses_real_wire_decode_and_amplitude_buffer():
    import numpy as np
    buffer = AmplitudeBuffer(keep_all=True)
    source = RawWgspReplaySource(buffer)
    source.start()
    try:
        deadline = time.monotonic() + 5
        while buffer.total_frames < 32 and source.error is None and time.monotonic() < deadline:
            time.sleep(.025)
        assert source.error is None
        assert buffer.total_frames >= 32
        times, amps = buffer.get_window(1)
        assert amps.shape[1] == 245
        assert np.isfinite(amps).all()
        assert np.allclose(np.diff(times), 1 / 320, atol=1e-6)
        assert source.status()["raw_wire_decode"]
        assert source.status()["raw_batches"] > 0
    finally:
        source.stop()
        source.join(1)
