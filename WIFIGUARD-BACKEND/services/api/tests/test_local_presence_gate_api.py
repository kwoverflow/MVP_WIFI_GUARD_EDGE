"""Real isolated DB/API routing; notification adapter is deliberately mocked."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from helpers import create_device, signup_home
from sqlalchemy import select
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult, StatusRecord
from wifiguard_contracts.mqtt import PresenceMsg, TelemetryMsg, LinkStats, LoopStats
from wifiguard_db.models import EventLog, FallEvent


def test_presence_pause_reentry_retains_real_fall_history_and_notifies_once(
    monkeypatch,
    migrated_db,
    db,
):
    monkeypatch.setenv("LOCAL_EDGE_TOKEN", "g" * 48)
    for name in ("MQTT_HOST", "KAFKA_BOOTSTRAP", "TSDB_DSN"):
        monkeypatch.setenv(name, "")
    from wifiguard_api.app import create_app

    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        home = signup_home(client, "gate-routing@example.invalid")
        device = create_device(client, home)
        device_id, tenant = UUID(device["id"]), f"home-{home.user['id']}"
        now = datetime.now(UTC)
        notifications = []
        service = client.app.state.ingest
        monkeypatch.setattr(
            service.fall_notification, "notify", lambda r: notifications.append(r) or 1
        )
        headers = {"Authorization": "Bearer " + "g" * 48}

        def send(topic, record):
            body = {"topic": topic, "record": record.model_dump(mode="json")}
            response = client.post("/internal/edge/record", json=body, headers=headers)
            assert response.status_code == 200 and response.json()["accepted"]

        def presence(state, seq):
            at = now + timedelta(seconds=seq)
            send(
                topics.KAFKA_FEATURE_STREAM,
                FeatureRecord(
                    received_at=at,
                    source_topic="local/presence",
                    device_id=device_id,
                    tenant_id=tenant,
                    payload=PresenceMsg(
                        device_id=device_id,
                        tenant_id=tenant,
                        ts=at,
                        seq=seq,
                        tick_uptime_s=seq,
                        state=state,
                    ),
                ),
            )

        def fall(seq):
            at = now + timedelta(seconds=seq)
            return InferenceResult(
                device_id=device_id,
                tenant_id=tenant,
                ts=at,
                inferred_at=at,
                seq=seq,
                proba_fall=0.99,
                threshold=0.5,
                decision=True,
                postprocess="segmentation_b",
                model_version="routing-test-only",
            )

        presence("present", 1)
        first = fall(2)
        send(topics.KAFKA_INFERENCE_RESULT, first)
        assert len(notifications) == 1
        presence("absent", 3)
        assert service.cache.get(device_id).presence["state"] == "absent"
        assert service.cache.get(device_id).fall is not None
        send(topics.KAFKA_TELEMETRY, StatusRecord(received_at=now, source_topic="local/telemetry",
             device_id=device_id, tenant_id=tenant, payload=TelemetryMsg(
             device_id=device_id, tenant_id=tenant, ts=now, seq=3,
             link=LinkStats(connected=True, transport="replay"), presence_loop=LoopStats(enabled=True),
             feature_loop=LoopStats(enabled=True), gate_open=False)))
        assert service.cache.get(device_id).fall is None
        # Retry a historical result while paused: ACK, no second event/notification.
        send(topics.KAFKA_INFERENCE_RESULT, first)
        assert service.cache.get(device_id).fall is None
        presence("present", 4)
        second = fall(5)
        send(topics.KAFKA_INFERENCE_RESULT, second)
        send(topics.KAFKA_INFERENCE_RESULT, second)
        assert service.cache.get(device_id).fall["fall_count"] == 2
        assert len(notifications) == 2
        rows = (
            db.execute(select(FallEvent).where(FallEvent.device_id == device_id))
            .scalars()
            .all()
        )
        logs = (
            db.execute(
                select(EventLog).where(
                    EventLog.device_id == device_id, EventLog.level == "FALL"
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == len(logs) == 2
