"""인제스트 조립 — 브리지·컨슈머·싱크·캐시·허브의 수명을 한 곳에서 관리한다.

`services/api` 의 lifespan 과 `python -m wifiguard_ingest` 가 **같은 `start_all()`** 을
부른다. 그래서 나중에 프로세스를 쪼갤 때 코드를 바꿀 필요가 없다.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .cache import LiveCache, LiveHub
from .consumers import IngestConsumer
from .fall_sink import FallEventSink
from .fall_state import FallStateManager
from .fall_notification import FallNotificationDispatcher
from .mqtt_bridge import MqttBridge
from .presence_sink import PresenceSink
from .settings import IngestSettings
from .telemetry_sink import TelemetrySink

log = logging.getLogger("ingest.service")

#: 끊긴 기기를 찾아 online=False 로 내리는 주기.
SWEEP_INTERVAL_S = 5.0


class IngestService:
    """설정에 따라 켜지는 갈래만 기동한다. 브로커가 없어도 API 는 떠야 한다."""

    def __init__(self, settings: IngestSettings | None = None) -> None:
        self.settings = settings or IngestSettings.from_env()
        self.cache = LiveCache(offline_after_s=self.settings.offline_after_s)
        self.hub = LiveHub()
        self.presence_sink: PresenceSink | None = None
        self.telemetry_sink = TelemetrySink()
        self.fall_sink = FallEventSink()
        self.fall_state = FallStateManager(self.settings.fall_cooldown_seconds)
        self.fall_notification = FallNotificationDispatcher()
        self.bridge: MqttBridge | None = None
        self.consumer: IngestConsumer | None = None
        self._sweep_stop = threading.Event()
        self._sweep_thread: threading.Thread | None = None
        self._started = False

    # ── 수명 ────────────────────────────────────────────────────────
    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        if self._started:
            return
        s = self.settings
        if s.local_enabled:
            if len(s.local_edge_token.encode("utf-8")) < 32 or "REPLACE" in s.local_edge_token:
                raise ValueError("LOCAL_EDGE_TOKEN must be at least 32 bytes")
            if s.mqtt_enabled or s.kafka_enabled:
                raise ValueError("local ingest cannot run alongside MQTT/Kafka ingest")
        log.info("인제스트 기동: %s", s.describe())

        if loop is not None:
            self.hub.bind_loop(loop)

        if s.presence_persist_enabled:
            self.presence_sink = PresenceSink(
                s.tsdb_dsn,
                batch_size=s.presence_batch_size,
                flush_interval_s=s.presence_flush_interval_s,
            )
            self.presence_sink.start()

        if s.kafka_enabled or s.local_enabled:
            from .local import LocalConsumer
            consumer_class = LocalConsumer if s.local_enabled else IngestConsumer
            self.consumer = consumer_class(
                s,
                cache=self.cache,
                hub=self.hub,
                presence_sink=self.presence_sink,
                telemetry_sink=self.telemetry_sink,
                fall_sink=self.fall_sink,
                fall_state=self.fall_state,
                fall_notification=self.fall_notification,
            )
            if s.kafka_enabled:
                self.consumer.start()

        if s.mqtt_enabled:
            self.bridge = MqttBridge(s)
            self.bridge.start()

        if s.any_enabled:
            self._sweep_thread = threading.Thread(
                target=self._sweep_loop, daemon=True, name="ingest-sweep"
            )
            self._sweep_thread.start()

        self._started = True

    def stop(self) -> None:
        """기동의 역순 — 생산자(브리지)를 먼저 끊고 소비자를 정리한다."""
        if not self._started:
            return
        log.info("인제스트 정지 중…")
        self._sweep_stop.set()
        if self._sweep_thread is not None:
            self._sweep_thread.join(timeout=2.0)
        if self.bridge is not None:
            self.bridge.stop()
        if self.consumer is not None:
            self.consumer.stop()
        if self.presence_sink is not None:
            self.presence_sink.stop()   # 남은 배치를 비우고 끝낸다
        self.fall_notification.stop()
        self._started = False
        log.info("인제스트 정지 완료: %s", self.status())

    # ── 스윕 ────────────────────────────────────────────────────────
    def _sweep_loop(self) -> None:
        """텔레메트리가 끊긴 기기를 online=False 로 내린다.

        기기가 사라지면 아무 메시지도 오지 않으므로 이벤트만으로는 영원히 online 이다.
        화면이 "연결됨"으로 남아 있으면 안 되니 주기적으로 훑는다.
        """
        already_offline: set = set()
        while not self._sweep_stop.wait(SWEEP_INTERVAL_S):
            try:
                for device_id, state in self.cache.snapshot().items():
                    offline = state.online(self.settings.offline_after_s) is False
                    if offline and device_id not in already_offline:
                        already_offline.add(device_id)
                        self.telemetry_sink.mark_offline(device_id)
                        self.hub.publish_from_thread(state)  # 화면이 "연결 끊김"으로 바뀌게
                    elif not offline:
                        already_offline.discard(device_id)
            except Exception:
                log.exception("스윕 실패")

    # ── 관측 ────────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.describe(),
            "cache": self.cache.stats(),
            "subscribers": self.hub.subscriber_count,
            "bridge": self.bridge.status() if self.bridge else None,
            "consumer": self.consumer.status() if self.consumer else None,
            "presence_sink": self.presence_sink.status() if self.presence_sink else None,
            "telemetry_sink": self.telemetry_sink.status(),
            "fall_sink": self.fall_sink.status(),
            "fall_notification": self.fall_notification.status(),
        }


#: 프로세스 전역 인스턴스. API lifespan 과 `__main__` 이 공유한다.
_service: IngestService | None = None


def start_all(settings: IngestSettings | None = None, loop: asyncio.AbstractEventLoop | None = None) -> IngestService:
    global _service
    if _service is None:
        _service = IngestService(settings)
    _service.start(loop)
    return _service


def get_service() -> IngestService | None:
    """REST 핸들러가 캐시를 읽을 때 쓴다. 기동 전이면 None."""
    return _service


def stop_all() -> None:
    global _service
    if _service is not None:
        _service.stop()
        _service = None


def run_forever(settings: IngestSettings | None = None) -> None:
    """별도 프로세스로 돌릴 때(`python -m wifiguard_ingest`).

    이 경로에는 asyncio 루프가 없으므로 WS 팬아웃이 동작하지 않는다 — 그때는 Redis pub/sub
    이 필요하다(M6). 지금은 적재만 하는 용도다.
    """
    service = start_all(settings)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
