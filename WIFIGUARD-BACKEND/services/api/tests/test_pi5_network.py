import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import create_device, signup_home


@pytest.fixture
def wifi(monkeypatch, tmp_path, migrated_db):
    from wifiguard_api.routers import local_network as network
    from wifiguard_api.routers.local_control import atomic_state
    monkeypatch.setenv("PI5_WIFI_ENABLED", "1")
    monkeypatch.setenv("PI5_CONTROL_ENABLED", "1")
    monkeypatch.setenv("PI5_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LOCAL_EDGE_TOKEN", "w" * 48)
    monkeypatch.delenv("LOCAL_DEVICE_ID", raising=False)
    monkeypatch.delenv("LOCAL_TENANT_ID", raising=False)
    atomic_state(tmp_path / "control-agent.json", {"updated_at_unix": time.time(), "edge_state": "inactive"})
    calls = []
    tx = {"id": str(uuid4()), "phase": "awaiting_confirmation", "deadline": time.time() + 110, "error": None}
    def broker(value):
        calls.append(value)
        if value["action"] == "status":
            return {"interfaces": [{"name": "wlan0", "connected": True, "addresses": ["192.0.2.5"]}],
                    "ssid": "wifi", "autoconnect": False, "edge_stopped": True,
                    "updated_at_unix": time.time(), "transaction": None}
        if value["action"] == "scan": return {"networks": []}
        return {"transaction": tx}
    monkeypatch.setattr(network, "broker", broker)
    from wifiguard_api.app import create_app
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        home = signup_home(client, "wifi-owner@example.invalid")
        dev = create_device(client, home, connection="SPI")
        assert client.post("/api/v1/local-edge/bind", headers=home.h, json={"device_id": dev["id"]}).status_code == 200
        yield client, home, dev, calls, network, tx


def test_wifi_requires_auth_and_bound_device_owner(wifi):
    client, home, _, calls, *_ = wifi
    assert client.get("/api/v1/local-edge/network/status").status_code == 401
    other = signup_home(client, "wifi-other@example.invalid")
    assert client.get("/api/v1/local-edge/network/status", headers=other.h).status_code == 404
    assert not calls
    value = client.get("/api/v1/local-edge/network/status", headers=home.h).json()
    assert value["available"] and not value["secure_credentials_transport"]


def test_http_cannot_submit_password_even_if_forged_forwarded_proto(wifi):
    client, home, _, calls, *_ = wifi
    response = client.post("/api/v1/local-edge/network/connect", headers={**home.h, "X-Forwarded-Proto": "https"},
                           json={"ssid": "wifi", "password": "DONT-ECHO-ME"})
    assert response.status_code == 403 and "DONT-ECHO-ME" not in response.text
    assert not calls


def test_secure_connect_and_no_secret_in_response_or_event_log(wifi, db):
    client, home, _, calls, *_ = wifi
    headers = {**home.h, "X-Wifiguard-Network-Transport": "loopback"}
    response = client.post("/api/v1/local-edge/network/connect", headers=headers,
                           json={"ssid": "wifi", "password": "DONT-ECHO-ME"})
    assert response.status_code == 200, response.text
    assert calls[-1]["password"] == "DONT-ECHO-ME"
    assert "DONT-ECHO-ME" not in response.text
    from sqlalchemy import select
    from wifiguard_db.models import EventLog
    assert all("DONT-ECHO-ME" not in log.msg for log in db.scalars(select(EventLog)).all())


def test_remote_peer_cannot_forge_trusted_transport():
    from starlette.requests import Request
    from wifiguard_api.routers.local_network import secure_transport
    request = Request({"type": "http", "client": ("192.0.2.10", 5555),
                       "headers": [(b"x-wifiguard-network-transport", b"https")]})
    assert not secure_transport(request)


def test_wifi_extra_fields_and_password_validation_redacted(wifi):
    client, home, *_ = wifi
    for body in ({"ssid": "wifi", "password": "short"}, {"ssid": "wifi", "password": "PRIVATE-SECRET", "interface": "eth0"}):
        response = client.post("/api/v1/local-edge/network/connect", headers=home.h, json=body)
        assert response.status_code == 422
        assert body["password"] not in response.text


def test_pending_wifi_blocks_start_but_allows_stop(wifi, monkeypatch):
    client, home, dev, _, network, tx = wifi
    monkeypatch.setattr(network, "broker", lambda _: {"transaction": tx})
    for action in ("start", "recalibrate"):
        response = client.post("/api/v1/local-edge/commands", headers=home.h, json={"device_id": dev["id"], "action": action})
        assert response.status_code == 409
    assert client.post("/api/v1/local-edge/commands", headers=home.h, json={"device_id": dev["id"], "action": "stop"}).status_code == 202


def test_disabled_cloud_profile_keeps_wifi_hidden(client):
    home = signup_home(client, "wifi-cloud@example.invalid")
    response = client.get("/api/v1/local-edge/network/status", headers=home.h)
    assert response.json()["enabled"] is False
    assert client.post("/api/v1/local-edge/network/scan", headers=home.h).status_code == 404


@pytest.mark.parametrize("error_status", [409, 503])
def test_agent_failure_not_shown_as_connected(wifi, monkeypatch, error_status):
    client, home, _, _, network, _ = wifi
    from wifiguard_api.errors import ApiError
    def fail(_): raise ApiError(error_status, "WIFI_AGENT_OFFLINE", "offline")
    monkeypatch.setattr(network, "broker", fail)
    value = client.get("/api/v1/local-edge/network/status", headers=home.h).json()
    assert not value["available"] and not value["interfaces"]
