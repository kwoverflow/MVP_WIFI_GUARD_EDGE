from types import SimpleNamespace

import pytest

from wifiguard_edge.presence import PresenceConfig
from wifiguard_edge.presence_loop import PresenceLoop
from wifiguard_local_edge.pi5 import MeasuredPresence


def fixture(monkeypatch):
    from wifiguard_local_edge import pi5
    clock = [10.0]
    calls = []
    monkeypatch.setattr(pi5.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(PresenceLoop, "_compute_wander_current",
                        lambda loop, cfg: calls.append(clock[0]) or len(calls) * .1)
    presence = MeasuredPresence(SimpleNamespace(device_resets=0), PresenceConfig(),
                                wander_stride_sec=1)
    return presence, clock, calls


def test_psd_runs_once_per_second_with_bounded_sample_hold(monkeypatch):
    loop, clock, calls = fixture(monkeypatch)
    values = []
    for now in (10, 10.5, 11, 11.5, 12):
        clock[0] = now
        values.append(loop._compute_wander_current(loop.config))
    assert calls == [10, 11, 12]
    assert values == pytest.approx([.1, .1, .2, .2, .3])
    metrics = loop.metrics()
    assert metrics["wander_compute_calls"] == 3 and metrics["wander_cache_hits"] == 2


def test_reset_calibration_and_clock_changes_invalidate_cache(monkeypatch):
    loop, clock, calls = fixture(monkeypatch)
    loop._compute_wander_current(loop.config)
    clock[0] += .1
    loop.config.wander_baseline *= 2
    loop._compute_wander_current(loop.config)
    loop._invalidate("sensor reset")
    loop._compute_wander_current(loop.config)
    clock[0] -= 2
    loop._compute_wander_current(loop.config)
    assert len(calls) == 4


def test_failed_psd_never_reuses_previous_result(monkeypatch):
    loop, clock, calls = fixture(monkeypatch)
    loop._compute_wander_current(loop.config)
    clock[0] += 1
    def fail(*_):
        raise RuntimeError("PSD failed")
    monkeypatch.setattr(PresenceLoop, "_compute_wander_current", fail)
    # The enclosing measured tick catches this and invalidates the presence gate.
    with pytest.raises(RuntimeError):
        loop._compute_wander_current(loop.config)


def test_same_cadence_does_not_cache_across_ticks(monkeypatch):
    loop, clock, calls = fixture(monkeypatch)
    loop.wander_stride_sec = .5
    for now in (10, 10.499, 10.998):
        clock[0] = now
        loop._compute_wander_current(loop.config)
    assert len(calls) == 3


def test_jitter_does_not_reduce_psd_rate_and_stale_cache_is_not_held(monkeypatch):
    loop, clock, calls = fixture(monkeypatch)
    for now in (10, 10.499, 10.998, 11.499, 11.997, 12.5, 14.5):
        clock[0] = now
        loop._compute_wander_current(loop.config)
    assert calls == [10, 10.998, 11.997, 14.5]
