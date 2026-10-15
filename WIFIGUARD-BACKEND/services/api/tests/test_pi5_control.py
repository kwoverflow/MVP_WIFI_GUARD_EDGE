import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import create_device, signup_home, signup_root, signup_member, invite_code
from sqlalchemy import select
from wifiguard_db.models import Device, EventLog


@pytest.fixture
def control(monkeypatch, tmp_path, migrated_db):
    monkeypatch.setenv("PI5_CONTROL_ENABLED", "1")
    monkeypatch.setenv("PI5_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LOCAL_EDGE_TOKEN", "p" * 48)
    monkeypatch.delenv("LOCAL_DEVICE_ID", raising=False)
    monkeypatch.delenv("LOCAL_TENANT_ID", raising=False)
    for name in ("MQTT_HOST", "KAFKA_BOOTSTRAP", "TSDB_DSN"):
        monkeypatch.setenv(name, "")
    from wifiguard_api.app import create_app
    from wifiguard_api.routers.local_control import atomic_state
    atomic_state(tmp_path / "control-agent.json", {"updated_at_unix": time.time(), "edge_state": "inactive"})
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        home = signup_home(client, "pi5-control@example.invalid")
        device = create_device(client, home, connection="SPI")
        yield client, home, device, tmp_path, atomic_state


def bind(control):
    client, home, device, *_ = control
    response = client.post("/api/v1/local-edge/bind", headers=home.h, json={"device_id": device["id"]})
    assert response.status_code == 200, response.text
    return client, home, device


def test_original_profile_does_not_expose_control(client):
    home = signup_home(client, "pi3-control@example.invalid")
    assert client.get("/api/v1/local-edge/status", headers=home.h).json() == {"enabled": False}
    assert client.post("/api/v1/local-edge/commands", headers=home.h,
                       json={"device_id": str(uuid4()), "action": "start"}).status_code == 404


def test_spi_binding_scope_and_command_allowlist(control):
    client, home, device = bind(control)
    other = signup_home(client, "pi5-other@example.invalid")
    assert client.get("/api/v1/local-edge/status", headers=other.h).status_code == 404
    assert client.get("/api/v1/local-edge/status").status_code == 401
    assert client.post("/api/v1/local-edge/commands", headers=home.h,
                       json={"device_id": device["id"], "action": "shell"}).status_code == 422
    response = client.post("/api/v1/local-edge/commands", headers=home.h,
                           json={"device_id": device["id"], "action": "start"})
    assert response.status_code == 202
    _, _, _, directory, _ = control
    queued = json.loads((directory / "commands" / f'{response.json()["id"]}.json').read_text())
    assert queued["device_id"] == device["id"] and queued["tenant_id"] == f'home-{home.user["id"]}'
    assert "token" not in queued and "unit" not in queued
    assert client.post("/api/v1/local-edge/bind", headers=home.h, json={"device_id": device["id"]}).status_code == 409


def test_real_spi_baseline_cannot_be_faked_by_ui(control):
    client, home, device = bind(control)
    assert client.patch(f'/api/v1/devices/{device["id"]}', headers=home.h,
                        json={"calibrationStage": "DONE", "presenceMvThreshold": .3}).status_code == 409
    assert client.delete(f'/api/v1/devices/{device["id"]}', headers=home.h).status_code == 409
    assert client.patch(f'/api/v1/devices/{device["id"]}', headers=home.h,
                        json={"name": "real sensor"}).status_code == 200


def test_fresh_confirmation_requires_matching_run_and_operator_assertion(control):
    client, home, device = bind(control)
    _, _, _, directory, write = control
    run = str(uuid4())
    write(directory / "startup-calibration.json", {"run_id": run, "phase": "waiting_empty_room"})
    body = {"device_id": device["id"], "action": "confirm-empty-room", "run_id": run, "empty_room_confirmed": True}
    assert client.post("/api/v1/local-edge/commands", json=body, headers=home.h).status_code == 409
    write(directory / "status.json", {"device_id": device["id"], "running": True, "updated_at_unix": time.time(),
                "pi5_services": {"startup_calibration": {"run_id": run}}})
    assert client.post("/api/v1/local-edge/commands", json={**body, "empty_room_confirmed": False}, headers=home.h).status_code == 409
    assert client.post("/api/v1/local-edge/commands", json=body, headers=home.h).status_code == 202


def test_settings_pending_until_runtime_revision_ack(control):
    client, home, device = bind(control)
    headers = {"Authorization": "Bearer " + "p" * 48}
    assert client.get("/internal/edge/config").status_code == 403
    first = client.get("/internal/edge/config", headers=headers).json()
    assert first["settings"] is None  # do not overwrite original/calibrated rules by defaults
    cfg = client.get("/api/v1/config", headers=home.h).json()
    cfg["threshold"] = .6
    assert client.put("/api/v1/config", json=cfg, headers=home.h).status_code == 200
    desired = client.get("/api/v1/local-edge/status", headers=home.h).json()["desired_revision"]
    assert desired != first["revision"]  # does not wait for agent's old cached file
    second = client.get("/internal/edge/config", headers=headers).json()
    assert second["revision"] == desired and second["settings"]["threshold"] == .6


def test_real_calibration_db_persistence_and_report_dedup(control, db):
    client, home, device = bind(control)
    run = str(uuid4())
    headers = {"Authorization": "Bearer " + "p" * 48}
    body = {"device_id": device["id"], "tenant_id": f'home-{home.user["id"]}',
            "calibration": {"run_id": run, "phase": "done", "presence_mv_threshold": .42, "wander_baseline": .08}}
    assert client.post("/internal/edge/runtime", json=body, headers=headers).status_code == 200
    assert client.post("/internal/edge/runtime", json=body, headers=headers).status_code == 200
    value = client.get(f'/api/v1/devices/{device["id"]}', headers=home.h).json()
    assert value["calibrationStage"] == "DONE" and value["calibrating"] is False
    assert value["presenceMvThreshold"] == .42 and value["wanderBaseline"] == .08
    logs = db.execute(select(EventLog).where(EventLog.msg.like("Pi5 실제 빈방 보정:%"))).scalars().all()
    assert len(logs) == 1
    body["tenant_id"] = "other"
    assert client.post("/internal/edge/runtime", json=body, headers=headers).status_code == 403


def test_member_cannot_control_edge(control):
    client, _, _, *_ = control
    root = signup_root(client, "pi5-root@example.invalid")
    member = signup_member(client, "pi5-member@example.invalid", invite_code(client, root))
    device = create_device(client, root, connection="SPI")
    assert client.post("/api/v1/local-edge/bind", json={"device_id": device["id"]}, headers=member.h).status_code == 403


def test_invalid_or_stale_resource_timestamps_are_unmeasured(control):
    client, home, _ = bind(control)
    _, _, _, directory, write = control
    write(directory / "stack-status.json", {"updated_at_unix": "bad", "complete": True})
    response = client.get("/api/v1/local-edge/status", headers=home.h)
    assert response.status_code == 200 and response.json()["resources"]["fresh"] is False


def test_calibration_elapsed_uses_current_runtime_and_rejects_old_run(control):
    client, home, device = bind(control)
    _, _, _, directory, write = control
    run = str(uuid4())
    write(directory / "startup-calibration.json", {
        "run_id": run, "phase": "measuring", "phase_elapsed_seconds": 0,
    })
    runtime = {
        "device_id": device["id"], "updated_at_unix": time.time(),
        "pi5_services": {"startup_calibration": {
            "run_id": run, "phase": "measuring", "phase_elapsed_seconds": 12.5,
        }},
    }
    write(directory / "status.json", runtime)
    response = client.get("/api/v1/local-edge/status", headers=home.h)
    assert response.status_code == 200
    assert response.json()["calibration"]["phase_elapsed_seconds"] == 12.5
    runtime["pi5_services"]["startup_calibration"]["run_id"] = str(uuid4())
    write(directory / "status.json", runtime)
    assert client.get("/api/v1/local-edge/status", headers=home.h).json()["calibration"] is None
