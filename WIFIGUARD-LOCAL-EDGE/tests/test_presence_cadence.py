from types import SimpleNamespace

import pytest

from wifiguard_edge.presence import PresenceConfig, PresenceDetector
from wifiguard_edge.presence_loop import PresenceLoop
from wifiguard_local_edge.pi5 import MeasuredPresence
from wifiguard_local_edge.presence_gate import PresenceInferenceGate


def test_pi5_default_does_not_change_original_pi3_cadence():
    ring = SimpleNamespace(device_resets=0)
    cfg = PresenceConfig()
    assert PresenceLoop(ring, cfg).stride_sec == 0.25
    measured = MeasuredPresence(ring, cfg)
    assert measured.stride_sec == 0.5
    assert cfg.window_sec == 3 and cfg.wander_window_sec == 10
    assert measured.metrics()["target_hz"] == 2


@pytest.mark.parametrize("period, misses", [(0.25, 1), (0.5, 0)])
def test_deadline_uses_selected_cadence(monkeypatch, period, misses):
    from wifiguard_local_edge import pi5

    ring = SimpleNamespace(
        device_resets=0,
        stats=lambda: {"device_resets": 0, "frame_age_seconds": 0.02},
    )
    presence = MeasuredPresence(ring, PresenceConfig(), stride_sec=period)
    times = iter([10.0, 10.3])
    monkeypatch.setattr(pi5.time, "perf_counter", lambda: next(times))

    def success(loop):
        loop._last = PresenceDetector().update(1, 100, 0)
        loop._last_error = None

    monkeypatch.setattr(PresenceLoop, "_tick", success)
    presence._tick()
    assert presence.metrics()["deadline_misses"] == misses
    assert presence.metrics()["stride_seconds"] == period


def test_half_second_presence_stays_fresh_and_stale_still_fails_closed():
    gate = PresenceInferenceGate()
    snapshot = {"state": "present", "last_error": None,
                "completed_at": 10.0, "epoch": 0, "revision": 1}
    stats = {"device_resets": 0, "frame_age_seconds": 0.02}
    assert gate.evaluate(snapshot, stats, source_running=True, now=10.5)["open"]
    assert gate.evaluate(snapshot, stats, source_running=True, now=10.751)["reason"] == "stale_presence"


@pytest.mark.parametrize("period", [0, -1, 1, float("nan"), float("inf")])
def test_unsupported_cadence_rejected(period):
    with pytest.raises(ValueError, match="presence stride"):
        MeasuredPresence(SimpleNamespace(device_resets=0), PresenceConfig(), stride_sec=period)
