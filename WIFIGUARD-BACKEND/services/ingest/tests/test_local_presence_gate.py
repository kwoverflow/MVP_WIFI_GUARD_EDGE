from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult, StatusRecord
from wifiguard_contracts.mqtt import LinkStats, LoopStats, PresenceMsg, TelemetryMsg
from wifiguard_ingest.cache import LiveCache, LiveHub
from wifiguard_ingest.fall_state import FallStateManager
from wifiguard_ingest.local import LocalConsumer
from wifiguard_ingest.settings import IngestSettings


class Sink:
    def __init__(self):
        self.rows = []

    def record(self, record):
        self.rows.append(record)
        return True

    def offer(self, *row):
        self.rows.append(row)

    def status(self):
        return {"duplicates": 0}


class Notifications:
    def __init__(self):
        self.sent = []

    def notify(self, result):
        self.sent.append(result)


@pytest.mark.parametrize("pause", ["absent", "unknown", "disconnected"])
def test_paused_session_retains_history_dedup_and_allows_next_fall(pause):
    device, tenant, now = uuid4(), "home-test", datetime.now(UTC)
    sink, presence_sink, notification = Sink(), Sink(), Notifications()
    cache = LiveCache()
    consumer = LocalConsumer(
        IngestSettings.from_env(),
        cache=cache,
        hub=LiveHub(),
        fall_state=FallStateManager(),
        fall_sink=sink,
        presence_sink=presence_sink,
        fall_notification=notification,
    )
    cache.apply_presence(device, tenant, {"state": "present"}, now)

    def inference(seq):
        at = now + timedelta(seconds=seq)
        return InferenceResult(
            device_id=device,
            tenant_id=tenant,
            seq=seq,
            ts=at,
            inferred_at=at,
            proba_fall=0.99,
            threshold=0.5,
            postprocess="segmentation_b",
            decision=True,
        )

    first = inference(1)
    assert consumer.handle(
        topics.KAFKA_INFERENCE_RESULT, first.model_dump_json().encode()
    )
    assert len(sink.rows) == len(notification.sent) == 1
    if pause == "absent":
        msg = PresenceMsg(
            device_id=device,
            tenant_id=tenant,
            ts=now,
            seq=2,
            tick_uptime_s=1,
            state="absent",
        )
        record = FeatureRecord(
            received_at=now,
            source_topic="local/presence",
            device_id=device,
            tenant_id=tenant,
            payload=msg,
        )
        topic = topics.KAFKA_FEATURE_STREAM
    else:
        msg = TelemetryMsg(
            device_id=device,
            tenant_id=tenant,
            ts=now,
            seq=2,
            link=LinkStats(connected=pause != "disconnected", transport="replay"),
            presence_loop=LoopStats(enabled=True, last_error="presence unavailable"),
            feature_loop=LoopStats(enabled=True),
            gate_open=False,
        )
        record = StatusRecord(
            received_at=now,
            source_topic="local/telemetry",
            device_id=device,
            tenant_id=tenant,
            payload=msg,
        )
        topic = topics.KAFKA_TELEMETRY
    assert consumer.handle(topic, record.model_dump_json().encode())
    state = cache.get(device)
    if pause == "absent":
        # Normal absence does not clear confirmation history during grace.
        assert state.fall is not None
        msg = TelemetryMsg(device_id=device, tenant_id=tenant, ts=now, seq=3,
                           link=LinkStats(connected=True, transport="replay"),
                           presence_loop=LoopStats(enabled=True), feature_loop=LoopStats(enabled=True),
                           gate_open=False)
        closed = StatusRecord(received_at=now, source_topic="local/telemetry",
                              device_id=device, tenant_id=tenant, payload=msg)
        assert consumer.handle(topics.KAFKA_TELEMETRY, closed.model_dump_json().encode())
        state = cache.get(device)
    assert state.fall is None
    if pause == "absent":
        assert state.presence["state"] == "absent"
        assert len(presence_sink.rows) == 1
    else:
        assert state.presence is None
    if pause == "disconnected":
        assert state.online(5) is False
    # Replaying a previously persisted fall must NOT notify twice after pause.
    consumer.handle(topics.KAFKA_INFERENCE_RESULT, first.model_dump_json().encode())
    assert len(sink.rows) == len(notification.sent) == 1
    second = inference(3)
    consumer.handle(topics.KAFKA_INFERENCE_RESULT, second.model_dump_json().encode())
    assert cache.get(device).fall["fall_count"] == 2
    assert len(sink.rows) == len(notification.sent) == 2
