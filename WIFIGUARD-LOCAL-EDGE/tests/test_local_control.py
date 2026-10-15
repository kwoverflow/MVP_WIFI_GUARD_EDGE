import hashlib
import json
import subprocess
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from wifiguard_edge.config import PresenceConfig
from wifiguard_local_edge.control import ControlAgent, LoopbackClient, read_binding, read_json
from wifiguard_local_edge.live_monitor import LiveStackMonitor, UNITS
from wifiguard_local_edge.metrics import atomic_json
from wifiguard_local_edge.runtime_config import RuntimeConfiguration


@pytest.fixture
def identity(tmp_path):
    value = {"device_id": str(uuid4()), "tenant_id": "home-test"}
    atomic_json(tmp_path / "device-binding.json", value)
    return value


def command(identity, action="start", **extra):
    return {**identity, "id": str(uuid4()), "action": action, "created_at_unix": time.time(), **extra}


@pytest.mark.parametrize("action,expected", [("start", "start"), ("stop", "stop"), ("recalibrate", "restart")])
def test_only_fixed_service_actions(tmp_path, identity, action, expected):
    calls = []
    agent = ControlAgent(tmp_path, None, executor=calls.append)
    assert agent.execute(command(identity, action))["state"] == "completed"
    assert calls == [expected]


@pytest.mark.parametrize("changes", [{"action": "rm -rf"}, {"created_at_unix": 0}, {"device_id": str(uuid4())}, {"tenant_id": "other"}])
def test_invalid_commands_never_execute(tmp_path, identity, changes):
    calls = []
    agent = ControlAgent(tmp_path, None, executor=calls.append)
    with pytest.raises(ValueError):
        agent.execute({**command(identity), **changes})
    assert not calls


def test_fresh_empty_room_confirmation_is_per_run(tmp_path, identity):
    run = str(uuid4())
    atomic_json(tmp_path / "startup-calibration.json", {"phase": "waiting_empty_room", "run_id": run})
    runtime = {**identity, "running": True, "updated_at_unix": time.time(),
               "pi5_services": {"startup_calibration": {"run_id": run}}}
    atomic_json(tmp_path / "status.json", runtime)
    agent = ControlAgent(tmp_path, None)
    value = command(identity, "confirm-empty-room", run_id=run, empty_room_confirmed=True)
    agent.execute(value)
    assert read_json(tmp_path / "empty-room-confirmation.json")["run_id"] == run
    runtime["pi5_services"]["startup_calibration"]["run_id"] = str(uuid4())
    atomic_json(tmp_path / "status.json", runtime)
    with pytest.raises(ValueError, match="stale"):
        agent.execute(value)


def test_queue_result_and_agent_heartbeat(tmp_path, identity):
    calls = []
    client = SimpleNamespace(request=lambda *a: {"bound": False})
    agent = ControlAgent(tmp_path, client, executor=calls.append, state_reader=lambda: "inactive")
    value = command(identity)
    atomic_json(tmp_path / "commands" / f'{value["id"]}.json', value)
    agent.cycle()
    assert calls == ["start"]
    assert not list((tmp_path / "commands").glob("*.json"))
    assert read_json(tmp_path / "last-command.json")["state"] == "completed"
    assert read_json(tmp_path / "control-agent.json")["edge_state"] == "inactive"


def test_unacknowledged_report_is_not_silently_successful(tmp_path, identity):
    run = str(uuid4())
    atomic_json(tmp_path / "status.json", {**identity, "running": True, "updated_at_unix": time.time(),
                "pi5_services": {"startup_calibration": {"run_id": run}}})
    atomic_json(tmp_path / "startup-calibration.json", {"phase": "done", "run_id": run})
    client = SimpleNamespace(request=lambda route, *a: {"bound": True, **identity} if route.endswith("config") else {"accepted": False})
    agent = ControlAgent(tmp_path, client, state_reader=lambda: "active")
    agent.cycle()
    assert read_json(tmp_path / "control-agent.json")["last_error"]


@pytest.mark.parametrize("base", ["https://example.com", "http://127.0.0.1.evil", "http://user@localhost", "http://localhost/internal"])
def test_management_token_cannot_leave_loopback(base):
    with pytest.raises(ValueError):
        LoopbackClient(base, "t" * 48)


def test_first_registration_can_have_no_binding(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCAL_DEVICE_ID", raising=False)
    monkeypatch.delenv("LOCAL_TENANT_ID", raising=False)
    with pytest.raises(ValueError):
        read_binding(tmp_path)


def write_config(tmp_path, identity, settings):
    payload = {"bound": True, **identity, "settings": settings}
    payload["revision"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    atomic_json(tmp_path / "runtime-config.json", payload)
    return payload


def runtime_parts():
    presence = SimpleNamespace(config=PresenceConfig())
    presence.apply_config = lambda config, rev: setattr(presence, "config", config)
    engine = SimpleNamespace(threshold=.468)
    trigger = SimpleNamespace(resets=0, ab_rules={"k": 4, "n": 4, "threshold": .25})
    trigger.reset = lambda: setattr(trigger, "resets", trigger.resets + 1)
    return presence, engine, trigger


def test_settings_ack_preserves_ab_rules_and_rejects_bad_hash(tmp_path, identity):
    parts = runtime_parts()
    cfg = RuntimeConfiguration(tmp_path, **identity)
    settings = {"presence_mv_threshold": .4, "wander_ratio_threshold": 2.0,
                "presence_timeout_s": 11, "threshold": .6, "cooldown_seconds": 12}
    payload = write_config(tmp_path, identity, settings)
    assert cfg.apply(*parts)
    assert parts[0].config.presence_timeout_s == 11 and parts[1].threshold == .6
    assert parts[2].ab_rules == {"k": 4, "n": 4, "threshold": .25}
    assert parts[2].resets == 1
    assert cfg.status()["applied_revision"] == payload["revision"]
    assert not cfg.apply(*parts)
    payload["settings"]["threshold"] = .7
    atomic_json(tmp_path / "runtime-config.json", {**payload, "revision": "bad"})
    assert not cfg.apply(*parts) and cfg.error
    assert parts[1].threshold == .6


def test_corrupt_settings_fail_closed_and_valid_copy_recovers(tmp_path, identity):
    cfg = RuntimeConfiguration(tmp_path, **identity)
    parts = runtime_parts()
    original = write_config(tmp_path, identity, None)
    assert cfg.apply(*parts)
    atomic_json(tmp_path / "runtime-config.json", {})
    assert not cfg.apply(*parts) and cfg.error
    atomic_json(tmp_path / "runtime-config.json", original)
    assert cfg.apply(*parts) and cfg.error is None


@pytest.mark.parametrize("value", [float("nan"), True, -1, 20])
def test_invalid_settings_never_mutate_presence_or_model(tmp_path, identity, value):
    parts = runtime_parts()
    before = replace(parts[0].config)
    settings = {"presence_mv_threshold": value, "wander_ratio_threshold": 2,
                "presence_timeout_s": 10, "threshold": .5, "cooldown_seconds": 10}
    write_config(tmp_path, identity, settings)
    cfg = RuntimeConfiguration(tmp_path, **identity)
    assert not cfg.apply(*parts) and cfg.error
    assert parts[0].config == before and parts[1].threshold == .468


def test_monitor_includes_all_services_and_uses_aligned_counters(tmp_path):
    clock = [0.0]
    group = lambda unit: {"ControlGroup": "/system.slice/" + unit, "MainPID": "1", "NRestarts": "0"}
    counters = lambda path: {"cpu_usec": int(clock[0] * 10000), "memory_bytes": 2**20,
                             "swap_bytes": 0, "io_read_bytes": 5, "io_write_bytes": 7}
    monitor = LiveStackMonitor(tmp_path, group_reader=group, counter_reader=counters, clock=lambda: clock[0])
    first = monitor.sample()
    assert first["total"]["cpu_percent_host_basis"] is None
    clock[0] = 2
    value = monitor.sample()
    assert value["complete"] and set(value["groups"]) == set(UNITS)
    assert value["total"]["cpu_percent_one_core_basis"] == len(UNITS)
    assert value["total"]["memory_mib"] == len(UNITS)
    assert value["model_gpu_used"] is False


def test_missing_or_overlapping_services_do_not_report_fake_zero(tmp_path):
    def missing(unit):
        raise subprocess.TimeoutExpired("systemctl", 3)
    value = LiveStackMonitor(tmp_path, group_reader=missing).sample()
    assert not value["complete"] and value["total"]["memory_mib"] is None
    group = lambda unit: {"ControlGroup": "/shared", "MainPID": "1", "NRestarts": "0"}
    counters = lambda _: {"cpu_usec": 0, "memory_bytes": 0, "swap_bytes": 0, "io_read_bytes": 0, "io_write_bytes": 0}
    value = LiveStackMonitor(tmp_path, group_reader=group, counter_reader=counters).sample()
    assert "overlap" in value["errors"] and not value["complete"]
