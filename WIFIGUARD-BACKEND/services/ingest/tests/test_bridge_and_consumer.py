"""MQTT 브리지의 신원 검증과 Kafka 컨슈머의 팬아웃.

브리지는 **기기가 주장하는 신원을 믿지 않는다.** 토픽에서 파싱한 값이 정본이고, 페이로드가
다른 값을 주장하면 버린다. 이게 없으면 자격증명이 샌 기기 하나가 다른 테넌트의 재실 이력을
위조할 수 있다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, StatusRecord
from wifiguard_contracts.mqtt import LinkStats, LoopStats, PresenceMsg, TelemetryMsg

from wifiguard_ingest.cache import LiveCache, LiveHub
from wifiguard_ingest.consumers import IngestConsumer
from wifiguard_ingest.mqtt_bridge import MqttBridge
from wifiguard_ingest.settings import IngestSettings

TENANT = f"home-{uuid4()}"
DEVICE = uuid4()


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bytes]] = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))

    def flush(self, timeout=None): ...
    def close(self, timeout=None): ...


@pytest.fixture
def settings() -> IngestSettings:
    return IngestSettings.from_env()


@pytest.fixture
def producer() -> FakeProducer:
    return FakeProducer()


@pytest.fixture
def bridge(settings, producer) -> MqttBridge:
    b = MqttBridge(settings, producer_factory=lambda: producer)
    b._producer = producer
    return b


def presence_msg(**over) -> PresenceMsg:
    base = dict(
        device_id=DEVICE, tenant_id=TENANT, ts=datetime.now(UTC), seq=1,
        tick_uptime_s=12.5, state="present", mv_current=3.1, mv_threshold=2.0,
        wander_current=0.9, wander_baseline=0.5, wander_ratio_threshold=1.8,
        wander_ratio=1.8, wander_confirmed=True, last_activity_at=1757000000.0,
        seconds_since_activity=0.25, just_changed=False,
    )
    return PresenceMsg(**{**base, **over})


def telemetry_msg(connected: bool = True) -> TelemetryMsg:
    loop = LoopStats(enabled=True, tick_count=10, skip_count=0)
    return TelemetryMsg(
        device_id=DEVICE, tenant_id=TENANT, ts=datetime.now(UTC), seq=2,
        link=LinkStats(connected=connected, transport="replay", frames_ok=100),
        presence_loop=loop, feature_loop=loop,
        gate_open=True, signals_published=40, signals_gated=3, uptime_s=30.0,
    )


# ── 브리지: 신원 검증 ────────────────────────────────────────────────
def test_valid_presence_is_forwarded(bridge, producer):
    topic = topics.leaf_topic(TENANT, DEVICE, "presence")
    assert bridge.handle(topic, presence_msg().model_dump_json().encode()) is True

    kafka_topic, key, value = producer.sent[0]
    assert kafka_topic == topics.KAFKA_FEATURE_STREAM
    assert key == str(DEVICE), "파티션 키가 device_id 여야 기기별 순서가 보존된다"

    record = FeatureRecord.model_validate_json(value)
    assert record.tenant_id == TENANT and record.device_id == DEVICE
    assert record.source_topic == topic


def test_payload_cannot_claim_another_device(bridge, producer):
    """★ 토픽은 내 것인데 페이로드가 남의 device_id 를 주장하는 경우."""
    topic = topics.leaf_topic(TENANT, DEVICE, "presence")
    forged = presence_msg(device_id=uuid4())
    assert bridge.handle(topic, forged.model_dump_json().encode()) is False
    assert producer.sent == []
    assert "device_id 불일치" in bridge.status()["last_error"]


def test_payload_cannot_claim_another_tenant(bridge, producer):
    """★ 다른 테넌트의 재실 이력을 위조하려는 경우."""
    topic = topics.leaf_topic(TENANT, DEVICE, "presence")
    forged = presence_msg(tenant_id=f"home-{uuid4()}")
    assert bridge.handle(topic, forged.model_dump_json().encode()) is False
    assert producer.sent == []
    assert "tenant 불일치" in bridge.status()["last_error"]


def test_telemetry_goes_to_its_own_topic(bridge, producer):
    bridge.handle(topics.leaf_topic(TENANT, DEVICE, "telemetry"), telemetry_msg().model_dump_json().encode())
    assert producer.sent[0][0] == topics.KAFKA_TELEMETRY


def test_downlink_leaf_is_not_forwarded(bridge, producer):
    """`cmd` 는 클라우드→엣지다. 브리지가 되돌려 올리면 루프가 된다."""
    assert bridge.handle(topics.leaf_topic(TENANT, DEVICE, "cmd"), b"{}") is False
    assert producer.sent == []


def test_malformed_topic_and_payload_are_rejected(bridge, producer):
    assert bridge.handle("nonsense/topic", b"{}") is False
    assert bridge.handle(topics.leaf_topic(TENANT, DEVICE, "presence"), b"not json") is False
    assert bridge.handle(topics.leaf_topic(TENANT, DEVICE, "presence"), b'{"kind":"presence"}') is False
    assert producer.sent == []
    assert bridge.status()["rejected"] == 3


# ── 컨슈머: 캐시·싱크·허브 ───────────────────────────────────────────
class FakeSink:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def offer(self, ts, tenant_id, device_id, fields):
        self.rows.append((ts, tenant_id, device_id, fields))


class FakeTelemetrySink:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def apply(self, device_id, last_seen_at, online=True):
        self.calls.append((device_id, last_seen_at, online))


@pytest.fixture
def wired(settings):
    cache, hub = LiveCache(offline_after_s=15.0), LiveHub()
    sink, tsink = FakeSink(), FakeTelemetrySink()
    consumer = IngestConsumer(
        settings, cache=cache, hub=hub, presence_sink=sink, telemetry_sink=tsink
    )
    return consumer, cache, hub, sink, tsink


def _record(msg, cls=FeatureRecord) -> bytes:
    return cls(
        received_at=datetime.now(UTC),
        source_topic=topics.leaf_topic(TENANT, DEVICE, msg.kind),
        tenant_id=TENANT, device_id=DEVICE, payload=msg,
    ).model_dump_json().encode()


def test_presence_reaches_sink_and_cache(wired):
    consumer, cache, _hub, sink, _ = wired
    assert consumer.handle(topics.KAFKA_FEATURE_STREAM, _record(presence_msg())) is True

    assert len(sink.rows) == 1
    _ts, tenant, device, fields = sink.rows[0]
    assert (tenant, device) == (TENANT, str(DEVICE))
    assert fields["state"] == "present"
    assert fields["seconds_since_activity"] == 0.25, "구 _payload() 가 버리던 필드"

    state = cache.get(DEVICE)
    assert state and state.presence["state"] == "present"


def test_presence_fields_match_db_columns(wired):
    """싱크로 넘어가는 키가 `presence_samples` 컬럼과 정확히 같아야 한다."""
    from wifiguard_ingest.presence_sink import COLUMNS

    consumer, _cache, _hub, sink, _ = wired
    consumer.handle(topics.KAFKA_FEATURE_STREAM, _record(presence_msg()))
    fields = sink.rows[0][3]
    assert set(fields) == set(COLUMNS) - {"ts", "facility_id", "device_id"}


def test_telemetry_updates_cache_and_sink(wired):
    consumer, cache, _hub, _sink, tsink = wired
    consumer.handle(topics.KAFKA_TELEMETRY, _record(telemetry_msg(), StatusRecord))

    state = cache.get(DEVICE)
    assert state and state.link["connected"] is True
    assert state.online(15.0) is True
    assert tsink.calls and tsink.calls[0][2] is True


def test_signal_is_not_consumed_here(wired):
    """모델서버 몫이다. 인제스트는 세기만 하고 싱크로 보내지 않는다."""
    from wifiguard_contracts.mqtt import SignalMsg

    consumer, _cache, _hub, sink, _ = wired
    msg = SignalMsg(
        device_id=DEVICE, tenant_id=TENANT, ts=datetime.now(UTC), seq=3,
        signal_b64="AAAAAA==", signal_len=1, fs_hz=166.75, window_samples=500,
        window_span_s=3.1, selected_subcarrier_count=30, selected_stream_count=12,
        selected_pc_indices="0;2", candidate_pc_count=3, selected_pc_count=2,
        input_frames=520, input_subcarriers=245, presence_state="present", gate_reason="present",
    )
    assert consumer.handle(topics.KAFKA_FEATURE_STREAM, _record(msg)) is True
    assert sink.rows == [], "signal 은 presence_samples 에 들어가지 않는다"
    assert consumer.status()["counts"]["signal"] == 1


def test_bad_record_does_not_kill_consumer(wired):
    consumer, _cache, _hub, _sink, _ = wired
    assert consumer.handle(topics.KAFKA_FEATURE_STREAM, b"not json") is False
    assert consumer.status()["errors"] == 1
    # 그다음 정상 레코드는 처리되어야 한다
    assert consumer.handle(topics.KAFKA_FEATURE_STREAM, _record(presence_msg())) is True


# ── 캐시: 3상태 online ───────────────────────────────────────────────
def test_online_is_tristate():
    cache = LiveCache(offline_after_s=15.0)
    assert cache.get(DEVICE) is None, "본 적 없는 기기는 캐시에 없다 → 응답에서 None"

    cache.apply_presence(DEVICE, TENANT, {"state": "present"}, datetime.now(UTC))
    state = cache.get(DEVICE)
    assert state.online(15.0) is None, "재실만 왔고 텔레메트리가 없으면 여전히 '모름'"

    cache.apply_telemetry(DEVICE, TENANT, {"connected": True}, datetime.now(UTC))
    assert cache.get(DEVICE).online(15.0) is True
    assert cache.get(DEVICE).online(-1.0) is False, "유예를 넘기면 끊김"


def test_cache_returns_copies():
    """호출자가 받은 dict 를 고쳐도 캐시가 오염되면 안 된다."""
    cache = LiveCache()
    cache.apply_presence(DEVICE, TENANT, {"state": "present"}, datetime.now(UTC))
    got = cache.get(DEVICE)
    got.presence["state"] = "absent"
    assert cache.get(DEVICE).presence["state"] == "present"
