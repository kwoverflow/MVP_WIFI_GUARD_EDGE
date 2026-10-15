import pytest
from wifiguard_local_edge.presence_gate import PresenceInferenceGate, PresenceInferencePaused


def evaluate(gate, now, state, *, changed=101, revision=2, calibration=None, **changes):
    snapshot = {"state": state, "completed_at": now, "state_changed_at": changed,
                "epoch": 0, "revision": revision, "last_error": None}
    snapshot.update(changes.pop("snapshot", {}))
    stats = {"frame_age_seconds": .01, "device_resets": 0}
    stats.update(changes.pop("stats", {}))
    return gate.evaluate(snapshot, stats, now=now, source_running=changes.get("running", True), calibration_phase=calibration)


def test_exact_ten_seconds_no_extension_and_lease_history_preserved():
    gate = PresenceInferenceGate()
    first = evaluate(gate, 100, "present", revision=1)
    for now in (101, 105, 110.999):
        current = evaluate(gate, now, "absent")
        assert current["open"] and current["reason"] == "absent_grace"
        gate.require(first, current)
        assert current["grace_remaining_seconds"] == pytest.approx(111 - now)
    closed = evaluate(gate, 111, "absent")
    assert not closed["open"] and closed["reason"] == "absent"
    with pytest.raises(PresenceInferencePaused):
        gate.require(first, closed)
    assert not evaluate(gate, 150, "absent")["open"]


@pytest.mark.parametrize("changes", [{"running": False}, {"stats": {"frame_age_seconds": .11}},
    {"stats": {"device_resets": 1}}, {"snapshot": {"last_error": "error"}},
    {"snapshot": {"completed_at": 0}}, {"calibration": "measuring"}])
def test_faults_close_grace_immediately_and_cannot_reuse_it(changes):
    gate = PresenceInferenceGate()
    evaluate(gate, 100, "present", revision=1)
    assert evaluate(gate, 101, "absent")["open"]
    assert not evaluate(gate, 102, "absent", **changes)["open"]
    assert not evaluate(gate, 103, "absent")["open"]


def test_unknown_or_initial_absence_never_gets_grace():
    gate = PresenceInferenceGate()
    assert not evaluate(gate, 101, "absent")["open"]
    evaluate(gate, 102, "present", revision=3)
    assert not evaluate(gate, 103, None, revision=4)["open"]
    assert not evaluate(gate, 104, "absent", revision=5)["open"]


def test_known_reentry_uses_same_session_but_missed_transition_invalidates():
    gate = PresenceInferenceGate()
    first = evaluate(gate, 100, "present", revision=1)
    evaluate(gate, 101, "absent")
    gate.require(first, evaluate(gate, 102, "present", revision=3))
    with pytest.raises(PresenceInferencePaused):
        gate.require(first, evaluate(gate, 103, "present", revision=5))
