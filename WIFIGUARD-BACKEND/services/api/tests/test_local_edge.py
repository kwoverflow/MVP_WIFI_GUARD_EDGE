from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import InferenceResult
from wifiguard_db.models import FallEvent

from helpers import create_device, signup_home


def configure(monkeypatch):
    monkeypatch.setenv("LOCAL_EDGE_TOKEN", "l"*48)
    for name in ("MQTT_HOST", "KAFKA_BOOTSTRAP", "TSDB_DSN"):
        monkeypatch.setenv(name, "")


def test_local_auth_identity_persistence_and_duplicate_ack(monkeypatch, migrated_db, db):
    configure(monkeypatch)
    from wifiguard_api.app import create_app
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        home = signup_home(client, "pi5-test@example.invalid")
        device = create_device(client, home)
        now = datetime.now(UTC)
        result = InferenceResult(device_id=UUID(device["id"]), tenant_id=f"home-{home.user['id']}",
                                 ts=now, inferred_at=now, seq=1, proba_fall=.9, threshold=.5,
                                 postprocess="segmentation_b", decision=True)
        body = {"topic": topics.KAFKA_INFERENCE_RESULT, "record": result.model_dump(mode="json")}
        assert client.post("/internal/edge/record", json=body).status_code == 403
        headers = {"Authorization": "Bearer " + "l"*48}
        assert client.post("/internal/edge/record", json=body, headers=headers).json() == {"accepted": True}
        assert client.post("/internal/edge/record", json=body, headers=headers).status_code == 200
        assert len(db.execute(select(FallEvent)).scalars().all()) == 1
        assert client.app.state.ingest.cache.get(UUID(device["id"])).fall["fall_count"] == 1
        body["record"]["tenant_id"] = "another-tenant"
        assert client.post("/internal/edge/record", json=body, headers=headers).status_code == 403


def test_lan_client_cannot_use_local_token(monkeypatch, migrated_db):
    configure(monkeypatch)
    from wifiguard_api.app import create_app
    with TestClient(create_app(), client=("192.168.1.10", 50000)) as client:
        response = client.post("/internal/edge/record", headers={"Authorization": "Bearer " + "l"*48},
                               json={"topic": topics.KAFKA_INFERENCE_RESULT, "record": {}})
        assert response.status_code == 403


def test_endpoint_is_disabled_in_original_pr_profile(client):
    assert client.post("/internal/edge/record", json={"topic": topics.KAFKA_INFERENCE_RESULT, "record": {}}).status_code == 404


def test_local_profile_rejects_default_jwt_secret(monkeypatch):
    import pytest
    from dataclasses import replace
    from wifiguard_api import app as module
    configure(monkeypatch)
    monkeypatch.setattr(module, "settings", replace(module.settings, jwt_secret="changeme"))
    with pytest.raises(RuntimeError, match="private JWT_SECRET"):
        module.create_app()


def test_local_api_uses_validated_record_without_second_json_parse(monkeypatch, migrated_db):
    configure(monkeypatch)
    from wifiguard_api.app import create_app
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        home = signup_home(client, "pi5-typed@example.invalid")
        device = create_device(client, home)
        now = datetime.now(UTC)
        result = InferenceResult(device_id=UUID(device["id"]), tenant_id=f"home-{home.user['id']}",
                                 ts=now, inferred_at=now, seq=1, proba_fall=.9, threshold=.5,
                                 postprocess="segmentation_b", decision=True)
        body = {"topic": topics.KAFKA_INFERENCE_RESULT, "record": result.model_dump(mode="json")}
        def redundant_parse(*args):
            raise AssertionError("local API must not serialize/reparse a validated record")
        monkeypatch.setattr(client.app.state.ingest.consumer, "handle", redundant_parse)
        headers = {"Authorization": "Bearer " + "l" * 48}
        assert client.post("/internal/edge/record", json=body, headers=headers).status_code == 200
        assert client.app.state.ingest.cache.get(UUID(device["id"])).fall["fall_count"] == 1
