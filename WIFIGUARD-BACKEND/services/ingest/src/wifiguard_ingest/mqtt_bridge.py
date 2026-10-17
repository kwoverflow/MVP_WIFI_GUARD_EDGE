"""MQTT → Kafka 브리지 (F-B01).

운영에서는 AWS IoT Rule 이 이 일을 대신한다. 로컬 개발과 자체 호스팅 Mosquitto 에서는
이 브리지가 필요하다.

## 페이로드의 신원 주장을 믿지 않는다

기기는 자기 토픽에만 발행할 수 있어야 하지만, 브로커 ACL 이 느슨하거나 자격증명이 새면
남의 토픽으로 쓸 수 있다. 그리고 페이로드 안의 `device_id`/`tenant_id` 는 **기기가 쓴 값**이다.

그래서 두 가지를 한다.
1. **토픽에서 파싱한 값**을 정본으로 삼는다(`FeatureRecord.device_id` 등 봉투 필드).
2. 페이로드가 주장하는 값과 다르면 **버린다**. 일치할 때만 통과시킨다.

이렇게 하면 한 기기가 다른 테넌트의 재실 이력을 위조할 수 없다. 봉투와 페이로드를
분리해 둔 것(`kafka._Receipt`)이 바로 이 검증을 표현하기 위해서다.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, StatusRecord
from wifiguard_contracts.mqtt import AckMsg, PresenceMsg, SignalMsg, TelemetryMsg

log = logging.getLogger("ingest.mqtt_bridge")

#: leaf → (메시지 모델, Kafka 토픽, 레코드 모델)
_ROUTES: dict[str, tuple[type, str, type]] = {
    "presence": (PresenceMsg, topics.KAFKA_FEATURE_STREAM, FeatureRecord),
    "signal": (SignalMsg, topics.KAFKA_FEATURE_STREAM, FeatureRecord),
    "telemetry": (TelemetryMsg, topics.KAFKA_TELEMETRY, StatusRecord),
    "ack": (AckMsg, topics.KAFKA_TELEMETRY, StatusRecord),
}


class MqttBridge:
    def __init__(self, settings: Any, producer_factory=None) -> None:
        self.settings = settings
        self._producer_factory = producer_factory
        self._client: Any = None
        self._producer: Any = None
        self._lock = threading.Lock()
        self._forwarded = 0
        self._rejected = 0
        self._errors = 0
        self._last_error: str | None = None
        self._connected = False

    # ── 수명 ────────────────────────────────────────────────────────
    def start(self) -> None:
        import paho.mqtt.client as mqtt

        self._producer = self._make_producer()

        s = self.settings
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wifiguard-ingest-bridge")
        if s.mqtt_username:
            client.username_pw_set(s.mqtt_username, s.mqtt_password or None)
        if s.mqtt_tls:
            client.tls_set(ca_certs=s.mqtt_ca_cert or None)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_async(s.mqtt_host, s.mqtt_port, keepalive=30)
        client.loop_start()
        self._client = client
        log.info("MQTT 브리지 기동 %s:%s", s.mqtt_host, s.mqtt_port)

    def _make_producer(self) -> Any:
        if self._producer_factory is not None:
            return self._producer_factory()
        if not self.settings.kafka_enabled:
            log.warning("KAFKA_BOOTSTRAP 미설정 — 브리지가 Kafka 로 전달하지 않는다")
            return None
        from kafka import KafkaProducer

        return KafkaProducer(
            bootstrap_servers=self.settings.kafka_bootstrap.split(","),
            value_serializer=lambda v: v,
            key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            linger_ms=20,          # 4Hz × 기기 수를 조금 모아 보낸다
            acks=1,
            retries=3,
        )

    def stop(self, timeout: float = 3.0) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                log.debug("mqtt 종료 중 예외", exc_info=True)
        if self._producer is not None:
            try:
                self._producer.flush(timeout=timeout)
                self._producer.close(timeout=timeout)
            except Exception:
                log.debug("kafka producer 종료 중 예외", exc_info=True)

    # ── 콜백 ────────────────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        ok = getattr(reason_code, "is_failure", None) is False or reason_code == 0
        self._connected = bool(ok)
        if ok:
            pattern = topics.subscribe_all_uplink()
            client.subscribe(pattern, qos=1)
            log.info("MQTT 구독: %s", pattern)
        else:
            log.warning("MQTT 연결 거부: %s", reason_code)

    def _on_disconnect(self, client, userdata, *args) -> None:
        self._connected = False
        log.warning("MQTT 연결 끊김 — 재연결 시도")

    def _on_message(self, client, userdata, message) -> None:
        del client, userdata
        try:
            self.handle(message.topic, message.payload)
        except Exception:
            with self._lock:
                self._errors += 1
            log.exception("브리지 처리 실패: %s", message.topic)

    # ── 본체 (테스트가 직접 부른다) ─────────────────────────────────
    def handle(self, topic: str, payload: bytes) -> bool:
        """검증 후 Kafka 로 전달. 통과하면 True."""
        try:
            parts = topics.parse(topic)
        except ValueError as exc:
            return self._reject(f"토픽 형식 아님 {topic!r}: {exc}")
        if parts.leaf not in _ROUTES:
            return self._reject(f"업링크 leaf 아님: {parts.leaf}")

        model, kafka_topic, record_model = _ROUTES[parts.leaf]
        try:
            msg = model.model_validate_json(payload)
        except ValidationError as exc:
            return self._reject(f"{parts.leaf} 페이로드 검증 실패: {exc.error_count()} 건")

        # ★ 신원 대조 — 페이로드의 주장과 토픽이 다르면 버린다
        if str(msg.device_id) != parts.device_id:
            return self._reject(
                f"device_id 불일치: 토픽 {parts.device_id} vs 페이로드 {msg.device_id}"
            )
        if msg.tenant_id != parts.tenant:
            return self._reject(f"tenant 불일치: 토픽 {parts.tenant} vs 페이로드 {msg.tenant_id}")

        record = record_model(
            received_at=datetime.now(UTC),
            source_topic=topic,
            tenant_id=parts.tenant,      # 토픽에서 파싱한 값이 정본이다
            device_id=msg.device_id,
            payload=msg,
        )
        if self._producer is not None:
            # 파티션 키 = device_id → 같은 기기의 순서가 보존된다.
            # presence 와 signal 이 한 토픽에 섞이는 이유이기도 하다(kafka.py docstring).
            self._producer.send(
                kafka_topic,
                key=parts.device_id,
                value=record.model_dump_json().encode("utf-8"),
            )
        with self._lock:
            self._forwarded += 1
        return True

    def _reject(self, reason: str) -> bool:
        with self._lock:
            self._rejected += 1
            self._last_error = reason
        log.warning("브리지 거부: %s", reason)
        return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "forwarded": self._forwarded,
                "rejected": self._rejected,
                "errors": self._errors,
                "last_error": self._last_error,
            }
