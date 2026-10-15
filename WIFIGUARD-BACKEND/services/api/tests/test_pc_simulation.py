import json

from helpers import signup_home
from test_pi5_control import control, bind  # shared fresh scoped fixture


def test_pc_endpoints_disabled_in_original_profile(client):
    home = signup_home(client, "pc-disabled@example.invalid")
    response = client.post("/api/v1/local-edge/simulation/scenario", headers=home.h, json={"scenario": "empty"})
    assert response.status_code == 404


def test_scoped_pc_raw_controls_allowlist_and_no_forced_decision(control, monkeypatch):
    client, home, device = bind(control)
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    assert client.post("/api/v1/local-edge/simulation/scenario", json={"scenario": "motion"}).status_code == 401
    other = signup_home(client, "pc-other@example.invalid")
    assert client.post("/api/v1/local-edge/simulation/scenario", headers=other.h, json={"scenario": "motion"}).status_code == 404
    for body in ({"scenario": "shell"}, {"scenario": "motion", "proba": .99}):
        assert client.post("/api/v1/local-edge/simulation/scenario", headers=home.h, json=body).status_code == 422
    response = client.post("/api/v1/local-edge/simulation/scenario", headers=home.h, json={"scenario": "motion"})
    assert response.status_code == 200 and response.json()["forced_model_decision"] is False
    assert json.loads((control[3] / "simulation-scenario.json").read_text())["scenario"] == "motion"
    monkeypatch.delenv("PC_SIMULATION_MODE")
    assert client.post("/api/v1/local-edge/simulation/scenario", headers=home.h, json={"scenario": "motion"}).status_code == 404


def test_alert_requires_explicit_confirmation_and_loopback_smtp(control, monkeypatch):
    client, home, device = bind(control)
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    for body in ({}, {"confirmed_test": False}, {"confirmed_test": True, "probability": .9}):
        assert client.post("/api/v1/local-edge/simulation/alert-test", headers=home.h, json=body).status_code == 422
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    assert client.post("/api/v1/local-edge/simulation/alert-test", headers=home.h, json={"confirmed_test": True}).status_code == 409


def test_pc_recipient_cannot_redirect_synthetic_alert_to_external_server(control, monkeypatch):
    client, home, device = bind(control)
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    monkeypatch.setenv("PC_SIMULATION_PUSH_URL", "http://127.0.0.1:15555")
    payload = {"name": "test", "role": "FAMILY", "push": True, "ntfyTopic": "validation-only", "ntfyServer": "https://ntfy.sh", "enabled": True}
    assert client.post("/api/v1/recipients", headers=home.h, json=payload).status_code == 422
    payload["ntfyServer"] = "http://127.0.0.1:15555"
    assert client.post("/api/v1/recipients", headers=home.h, json=payload).status_code == 201
