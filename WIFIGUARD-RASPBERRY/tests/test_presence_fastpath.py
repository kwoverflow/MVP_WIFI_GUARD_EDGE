"""Bit-identical amplitudes/statistics vs the pre-Pi5 resample row loop."""
import numpy as np
import pytest

from wifiguard_edge.presence.preprocessing import resample_signal


def reference(times, amp, fs=100, tolerance=2, max_steps=64):
    times, amp = times.astype(np.float64), amp.astype(np.float32)
    rows = [amp[0].astype(np.float32)]
    counts = dict(raw_samples=len(amp), resampled_samples=0, interp_steps=0,
                  fallback_steps=0, irregular_gaps=0, nonpositive_gaps=0)
    for i in range(1, len(amp)):
        gap = float(times[i]-times[i-1])
        if gap <= 0:
            rows.append(amp[i].astype(np.float32))
            counts["fallback_steps"] += 1
            counts["nonpositive_gaps"] += 1
            continue
        steps = max(1, int(round(gap/(1e6/fs))))
        if steps <= max_steps and abs(gap-steps*(1e6/fs)) <= tolerance*1000:
            for step in range(1, steps):
                alpha = step/steps
                rows.append(((1-alpha)*amp[i-1]+alpha*amp[i]).astype(np.float32))
                counts["interp_steps"] += 1
            rows.append(amp[i].astype(np.float32))
        else:
            rows.append(amp[i].astype(np.float32))
            counts["fallback_steps"] += 1
            counts["irregular_gaps"] += 1
    out = np.stack(rows).astype(np.float32)
    counts["resampled_samples"] = len(out)
    return out, counts


@pytest.mark.parametrize("hz", [100, 200, 320])
@pytest.mark.parametrize("mode", ["regular", "repeated", "backwards", "insert", "ties"])
def test_presence_resample_exact_parity(hz, mode):
    rng = np.random.default_rng(41)
    times = np.arange(80, dtype=np.float64)*1e6/hz
    if mode == "repeated":
        times[21] = times[20]
    elif mode == "backwards":
        times[21] = times[20]-1
    elif mode == "insert":
        times[40:] += 20000
    elif mode == "ties":
        times = np.arange(80, dtype=np.float64)*15000
    amps = rng.random((80, 245), dtype=np.float32)
    expected, counts = reference(times, amps)
    actual, observed = resample_signal(times, amps, fs_hz=100)
    np.testing.assert_array_equal(actual, expected)
    assert observed == counts
