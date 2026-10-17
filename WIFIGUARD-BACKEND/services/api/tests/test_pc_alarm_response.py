from uuid import UUID
from test_pi5_control import control, bind


def test_pc_alert_uses_committed_db_id_for_scoped_ui_response(control, monkeypatch):
    client, home, device = bind(control)
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    result = client.post("/api/v1/local-edge/simulation/alert-test", headers=home.h, json={"confirmed_test": True})
    assert result.status_code == 200 and result.json()["model_accuracy_verified"] is False
    falls = client.get("/api/v1/falls", headers=home.h).json()["items"]
    assert len(falls) == 1 and falls[0]["source"] == "SIMULATED"
    live = client.app.state.ingest.cache.get(UUID(device["id"]))
    assert str(live.fall["fall_event_id"]) == falls[0]["id"]
    response = client.patch(f'/api/v1/falls/{falls[0]["id"]}/response', headers=home.h, json={"response": "FALSE_ALARM"})
    assert response.status_code == 200 and response.json()["response"] == "FALSE_ALARM"
    assert response.json()["respondedAt"] is not None
