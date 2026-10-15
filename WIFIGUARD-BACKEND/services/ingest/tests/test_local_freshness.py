from datetime import UTC, datetime
from uuid import uuid4

from wifiguard_ingest.cache import LiveCache


def test_local_heartbeat_without_csi_cannot_leave_stale_presence_online():
    cache, device, now = LiveCache(), uuid4(), datetime.now(UTC)
    cache.apply_presence(device, "home-test", {"state": "present"}, now)
    cache.apply_fall(device, "home-test", {"detect_state": "FALL"}, now)
    state = cache.apply_telemetry(device, "home-test", {"connected": True}, now, input_required=True)
    assert state.online(5) is True and state.presence is not None
    for _ in range(3):
        state = cache.apply_telemetry(device, "home-test", {"connected": False}, now, input_required=True)
        assert state.online(5) is False
        assert state.presence is None and state.fall is None
    # Existing Pi3 broker heartbeat behavior remains opt-out/default.
    assert cache.apply_telemetry(device, "home-test", {"connected": False}, now).online(5) is True
