from datetime import UTC, datetime
from uuid import uuid4

import pytest

from wifiguard_contracts import topics
from wifiguard_contracts.kafka import InferenceResult
from wifiguard_ingest.cache import LiveCache, LiveHub
from wifiguard_ingest.fall_state import FallStateManager
from wifiguard_ingest.local import LocalConsumer
from wifiguard_ingest.service import IngestService
from wifiguard_ingest.settings import IngestSettings


class Sink:
    def __init__(self):
        self.available = False
        self.rows = []

    def record(self, result):
        if self.available:
            self.rows.append(result)
        return self.available

    def status(self):
        return {"duplicates": 0}


def test_db_failure_does_not_consume_fall_transition_or_deduplication():
    sink, cache, manager = Sink(), LiveCache(), FallStateManager()
    consumer = LocalConsumer(IngestSettings.from_env(), cache=cache, hub=LiveHub(),
                             fall_state=manager, fall_sink=sink)
    now = datetime.now(UTC)
    result = InferenceResult(device_id=uuid4(), tenant_id="home-test", ts=now, inferred_at=now,
                             seq=1, proba_fall=.9, threshold=.5, decision=True, postprocess="segmentation_b")
    raw = result.model_dump_json().encode()
    with pytest.raises(RuntimeError, match="persistence"):
        consumer.handle(topics.KAFKA_INFERENCE_RESULT, raw)
    assert cache.get(result.device_id) is None
    sink.available = True
    assert consumer.handle(topics.KAFKA_INFERENCE_RESULT, raw)
    assert cache.get(result.device_id).fall["fall_count"] == 1
    assert consumer.handle(topics.KAFKA_INFERENCE_RESULT, raw)
    assert len(sink.rows) == 1


def test_local_mode_does_not_start_mqtt_or_kafka(monkeypatch):
    monkeypatch.setenv("LOCAL_EDGE_TOKEN", "x"*48)
    monkeypatch.setenv("MQTT_HOST", "")
    monkeypatch.setenv("KAFKA_BOOTSTRAP", "")
    monkeypatch.setenv("TSDB_DSN", "")
    service = IngestService()
    try:
        service.start()
        assert isinstance(service.consumer, LocalConsumer)
        assert service.bridge is None
        assert service.consumer._thread is None
        assert service.status()["consumer"]["mode"] == "local-http"
    finally:
        service.stop()


def test_validated_local_dispatch_keeps_db_retry_and_duplicate_semantics():
    sink, cache, manager = Sink(), LiveCache(), FallStateManager()
    consumer = LocalConsumer(IngestSettings.from_env(), cache=cache, hub=LiveHub(),
                             fall_state=manager, fall_sink=sink)
    now = datetime.now(UTC)
    result = InferenceResult(device_id=uuid4(), tenant_id="home-test", ts=now, inferred_at=now,
                             seq=1, proba_fall=.9, threshold=.5, decision=True, postprocess="segmentation_b")
    assert consumer.handle_record(topics.KAFKA_TELEMETRY, result) is False
    assert consumer.handle_record("unknown", result) is False
    with pytest.raises(RuntimeError, match="persistence"):
        consumer.handle_record(topics.KAFKA_INFERENCE_RESULT, result)
    assert cache.get(result.device_id) is None
    sink.available = True
    assert consumer.handle_record(topics.KAFKA_INFERENCE_RESULT, result)
    assert consumer.handle_record(topics.KAFKA_INFERENCE_RESULT, result)
    assert len(sink.rows) == 1
    assert cache.get(result.device_id).fall["fall_count"] == 1


@pytest.mark.parametrize("variable,value", [("LOCAL_EDGE_TOKEN", "short"), ("KAFKA_BOOTSTRAP", "localhost:9092"), ("MQTT_HOST", "localhost")])
def test_invalid_local_config_fails_closed(monkeypatch, variable, value):
    monkeypatch.setenv("LOCAL_EDGE_TOKEN", "x"*48)
    monkeypatch.setenv("MQTT_HOST", "")
    monkeypatch.setenv("KAFKA_BOOTSTRAP", "")
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError):
        IngestService().start()
