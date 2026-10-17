import os
import time
import json
from types import SimpleNamespace

import pytest

from wifiguard_local_edge.buffer import AmplitudeBuffer
from wifiguard_local_edge.metrics import atomic_json
from wifiguard_local_edge.pc_demo import PcProcessMonitor, mobile_test_environment
from wifiguard_local_edge.replay import RawWgspReplaySource


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.025)
    assert predicate()


def test_scenarios_change_raw_not_decisions_and_disconnect_never_backfills(tmp_path):
    path = tmp_path / "scenario.json"
    atomic_json(path, {"scenario": "empty"})
    buffer = AmplitudeBuffer(keep_all=True)
    source = RawWgspReplaySource(buffer, scenario_file=path)
    source.start()
    try:
        wait_until(lambda: buffer.total_frames >= 40)
        _, amps = buffer.get_window(1)
        assert (amps == 30).all()
        atomic_json(path, {"scenario": "disconnect"})
        wait_until(lambda: source.scenario == "disconnect")
        before, seq = buffer.total_frames, source.packet_count
        time.sleep(.15)
        assert buffer.total_frames == before
        assert source.packet_count >= seq + 32
        assert source.status()["frames_ok"] == before
        atomic_json(path, {"scenario": "motion"})
        wait_until(lambda: buffer.total_frames >= before + 40)
        assert source.error is None
        assert source.status()["frames_ok"] == buffer.total_frames
        assert source.status()["frames_ok"] < source.packet_count
        _, amps = buffer.get_window(1)
        assert (amps != 30).any()
    finally:
        source.stop()
        source.join(1)


def test_pc_resource_scope_deduplicates_pids_and_marks_missing_as_unmeasured(tmp_path):
    monitor = PcProcessMonitor(tmp_path, {"control_monitor_inboxes": os.getpid(), "edge": None})
    first = monitor.sample()
    assert first["total"]["cpu_percent_host_basis"] is None
    time.sleep(.02)
    value = monitor.sample()
    assert value["complete"] and value["total"]["memory_mib"] > 0
    assert value["physical_pi5"] is False and value["model_gpu_used"] is False
    assert value["total"]["swap_mib"] is None
    assert value["total"]["memory_mib"] == value["groups"]["control_monitor_inboxes"]["memory_mib"]
    monitor.roots["api"] = -42
    value = monitor.sample()
    assert not value["complete"] and "api" in value["errors"]
    assert value["total"]["cpu_percent_host_basis"] is None
    assert value["total"]["memory_mib"] is None


def test_atomic_report_retries_transient_windows_reader_lock(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    original = Path.replace
    attempts = []
    def transient_replace(source, target):
        attempts.append(source)
        if len(attempts) < 3:
            raise PermissionError("temporary reader lock")
        return original(source, target)
    monkeypatch.setattr(Path, "replace", transient_replace)
    path = tmp_path / "status.json"
    atomic_json(path, {"ready": True})
    assert json.loads(path.read_text()) == {"ready": True}
    assert len(attempts) == 3 and not list(tmp_path.glob("*.tmp"))


def test_mobile_test_environment_defaults_off_even_without_credentials():
    assert mobile_test_environment(SimpleNamespace(mobile_test_push=False)) == {}


def fake_fcm_key(tmp_path):
    path = tmp_path / "mock-server-key.json"
    atomic_json(path, {"type": "service_account", "project_id": "mock-test-project",
                       "client_email": "sender@mock-test-project.iam.gserviceaccount.com",
                       "token_uri": "https://oauth2.googleapis.com/token", "private_key": "NOT-A-REAL-KEY"})
    return path


def test_mobile_test_environment_scopes_project_and_expires_in_one_hour(tmp_path):
    path = fake_fcm_key(tmp_path)
    args = SimpleNamespace(mobile_test_push=True, fcm_project_id="mock-test-project", fcm_key_file=path)
    env = mobile_test_environment(args, now=1000)
    assert env["MOBILE_PC_TEST_PUSH_EXPIRES_AT"] == "4600"
    assert env["FCM_PROJECT_ID"] == "mock-test-project" and env["FCM_SERVICE_ACCOUNT_FILE"] == str(path.resolve())
    assert env["MOBILE_PUSH_ENABLED"] == env["MOBILE_PC_TEST_PUSH_ENABLED"] == "1"
    assert "PC_SIMULATION_MODE" not in env  # Must never disable the shared PC guard.


@pytest.mark.parametrize("field,value", [("project_id", "wrong-project"), ("token_uri", "https://untrusted.invalid"),
                                         ("type", "authorized_user"), ("private_key", "")])
def test_mobile_test_environment_rejects_wrong_metadata_before_launch(tmp_path, field, value):
    path = fake_fcm_key(tmp_path)
    key = json.loads(path.read_text())
    key[field] = value
    atomic_json(path, key)
    args = SimpleNamespace(mobile_test_push=True, fcm_project_id="mock-test-project", fcm_key_file=path)
    with pytest.raises(ValueError, match="match the selected"):
        mobile_test_environment(args)


def test_mobile_test_environment_rejects_repo_stored_credentials(tmp_path, monkeypatch):
    from wifiguard_local_edge import pc_demo
    path = fake_fcm_key(tmp_path)
    monkeypatch.setattr(pc_demo, "ROOT", tmp_path)
    args = SimpleNamespace(mobile_test_push=True, fcm_project_id="mock-test-project", fcm_key_file=path)
    with pytest.raises(ValueError, match="outside the repository"):
        mobile_test_environment(args)
