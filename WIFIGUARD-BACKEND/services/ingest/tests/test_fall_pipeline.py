from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wifiguard_contracts import topics
from wifiguard_contracts.kafka import InferenceResult

from wifiguard_ingest.cache import LiveCache, LiveHub
from wifiguard_ingest.consumers import IngestConsumer
from wifiguard_ingest.fall_state import FallStateManager
from wifiguard_ingest.settings import IngestSettings


class FakeFallSink:
    def __init__(self) -> None:
        self.results = []

    def record(self, result):
        self.results.append(result)
        return True


class FakeFallNotification:
    def __init__(self) -> None:
        self.results = []

    def notify(self, result):
        self.results.append(result)
        return 1


def result(device_id, tenant_id, seq, probability, at):
    return InferenceResult(
        tenant_id=tenant_id,
        device_id=device_id,
        ts=at,
        seq=seq,
        proba_fall=probability,
        threshold=0.5,
        postprocess="none",
        inferred_at=at,
        model_version="test-model",
    )


def test_five_window_causal_vote_reaches_cache_and_sink_once():
    device_id, tenant_id = uuid4(), f"home-{uuid4()}"
    cache, hub, sink, notification = LiveCache(), LiveHub(), FakeFallSink(), FakeFallNotification()
    consumer = IngestConsumer(
        IngestSettings.from_env(),
        cache=cache,
        hub=hub,
        fall_state=FallStateManager(cooldown_seconds=10),
        fall_sink=sink,
        fall_notification=notification,
    )
    base = datetime.now(UTC)
    payloads = [result(device_id, tenant_id, i, 0.9, base + timedelta(milliseconds=250 * i)) for i in range(5)]
    for payload in payloads:
        assert consumer.handle(
            topics.KAFKA_INFERENCE_RESULT,
            payload.model_dump_json().encode(),
        )

    state = cache.get(device_id)
    assert state is not None
    assert state.fall["detect_state"] == "FALL"
    assert state.fall["postprocess"] == "causal_mode5"
    assert state.fall["fall_count"] == 1
    assert len(sink.results) == 1
    assert len(notification.results) == 1

    # Same Kafka value may be delivered again after a rebalance; it must be inert.
    consumer.handle(topics.KAFKA_INFERENCE_RESULT, payloads[-1].model_dump_json().encode())
    assert len(sink.results) == 1
    assert len(notification.results) == 1
    assert cache.get(device_id).fall["fall_count"] == 1


def test_devices_have_independent_vote_histories():
    manager = FallStateManager()
    base = datetime.now(UTC)
    first, second = uuid4(), uuid4()
    for seq in range(4):
        assert manager.apply(result(first, "tenant", seq, 0.9, base + timedelta(seconds=seq)))
    update = manager.apply(result(second, "tenant", 0, 0.9, base))
    assert update.fields["detect_state"] == "SUSPECT"
    assert update.fields["fall_count"] == 0


def test_segmentation_trigger_is_not_filtered_a_second_time():
    device_id = uuid4()
    base = datetime.now(UTC)
    payload = result(device_id, "tenant", 1, 0.4, base).model_copy(
        update={"postprocess": "segmentation_b", "decision": True}
    )
    update = FallStateManager().apply(payload)
    assert update is not None
    assert update.new_fall is True
    assert update.fields["detect_state"] == "FALL"
    assert update.fields["postprocess"] == "segmentation_b"
