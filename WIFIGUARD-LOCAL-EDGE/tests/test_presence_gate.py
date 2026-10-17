from types import SimpleNamespace

import numpy as np
import pytest

from wifiguard_local_edge.presence_gate import (
    PresenceInferenceGate,
    PresenceInferencePaused,
)
from wifiguard_local_edge.runtime import predict_window


def evaluate(
    gate,
    *,
    state="present",
    age=0.01,
    frame_age=0.02,
    running=True,
    error=None,
    epoch=0,
    source_epoch=0,
    revision=1,
):
    return gate.evaluate(
        {
            "state": state,
            "completed_at": 100 - age,
            "epoch": epoch,
            "revision": revision,
            "last_error": error,
        },
        {"frame_age_seconds": frame_age, "device_resets": source_epoch},
        source_running=running,
        now=100,
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"state": "absent"}, "absent"),
        ({"state": None}, "presence_unknown"),
        ({"state": "unexpected"}, "presence_unknown"),
        ({"running": False}, "sensor_disconnected"),
        ({"frame_age": 0.101}, "stale_csi"),
        ({"frame_age": float("nan")}, "stale_csi"),
        ({"age": 0.751}, "stale_presence"),
        ({"age": float("inf")}, "stale_presence"),
        ({"age": -1}, "stale_presence"),
        ({"error": "failed"}, "presence_error"),
        ({"source_epoch": 1}, "sensor_reset"),
    ],
)
def test_only_fresh_confirmed_presence_opens_gate(changes, reason):
    result = evaluate(PresenceInferenceGate(), **changes)
    assert result["open"] is False
    assert result["reason"] == reason


def test_reentry_or_missed_presence_transition_invalidates_previous_lease():
    gate = PresenceInferenceGate(linger_after_absent_seconds=0)
    first = evaluate(gate)
    assert first["open"]
    gate.require(first, evaluate(gate))
    closed = evaluate(gate, state="absent", revision=2)
    with pytest.raises(PresenceInferencePaused):
        gate.require(first, closed)
    reopened = evaluate(gate, revision=3)
    assert reopened["generation"] != first["generation"]
    with pytest.raises(PresenceInferencePaused):
        gate.require(first, reopened)
    # Even if the runtime missed both transitions, the producer revision changes.
    missed = evaluate(gate, revision=5)
    with pytest.raises(PresenceInferencePaused):
        gate.require(reopened, missed)


class Pipeline:
    def __init__(self):
        self.gate = PresenceInferenceGate(linger_after_absent_seconds=0)
        self.current = evaluate(self.gate)
        self.calls = []
        self.close_at = None
        self.last_timings_ms = {}

    def close(self, at):
        if self.close_at == at:
            self.current = evaluate(self.gate, state="absent", revision=2)

    def require_gate(self, lease):
        self.gate.require(lease, self.current)

    def build(self, amplitude, fs):
        self.calls.append("features")
        self.close("features")
        return SimpleNamespace(s3=[0], acf=[0])

    def predict(self, s3, acf):
        self.calls.append("model")
        self.close("model")
        return 0.99

    def update(self, probability):
        self.calls.append("postprocess")
        self.close("postprocess")
        return {"b_trigger": True}

    def inference(self, *args, lease):
        self.require_gate(lease)
        self.calls.append("enqueue")

    def run(self, lease):
        return predict_window(
            np.zeros((960, 30)),
            self,
            self,
            self,
            tick=0,
            preprocessed=0,
            frame_age=0.02,
            integration=self,
            lease=lease,
        )


@pytest.mark.parametrize(
    "close_at,expected",
    [
        ("before", []),
        ("features", ["features"]),
        ("model", ["features", "model"]),
        ("postprocess", ["features", "model", "postprocess"]),
    ],
)
def test_closed_or_changed_session_does_not_enqueue_or_alarm(close_at, expected):
    pipe = Pipeline()
    lease = pipe.current
    pipe.close_at = close_at
    pipe.close("before")
    with pytest.raises(PresenceInferencePaused):
        pipe.run(lease)
    assert pipe.calls == expected


def test_present_pipeline_preserves_probability_and_b_trigger():
    pipe = Pipeline()
    probability, decision, _ = pipe.run(pipe.current)
    assert probability == 0.99 and decision == {"b_trigger": True}
    assert pipe.calls == ["features", "model", "postprocess", "enqueue"]


def test_standalone_without_presence_integration_keeps_original_execution():
    pipe = Pipeline()
    probability, _, _ = predict_window(
        np.zeros((960, 30)),
        pipe,
        pipe,
        pipe,
        tick=0,
        preprocessed=0,
        frame_age=0.02,
    )
    assert probability == 0.99
    assert pipe.calls == ["features", "model", "postprocess"]


def test_gate_counters_distinguish_intentional_skips_and_suppressed_results():
    gate = PresenceInferenceGate()
    evaluate(gate, state="absent")
    gate.record_skip("absent")
    gate.record_skip("absent")
    gate.record_suppressed()
    assert gate.status()["gated_reasons"] == {"absent": 2}
    assert gate.status()["gated_ticks"] == 2
    assert gate.status()["suppressed_results"] == 1
