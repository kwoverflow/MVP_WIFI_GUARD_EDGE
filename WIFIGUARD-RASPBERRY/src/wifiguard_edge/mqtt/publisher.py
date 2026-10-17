"""MQTT 발행 (F-P12) — paho-mqtt, 아웃바운드 전용, 논블로킹 큐.

## 발행이 감지 루프를 막으면 안 된다

`enqueue()` 는 큐에 넣고 즉시 반환한다. 네트워크 왕복은 별도 스레드에서 일어난다.
이 구조는 `services/notification/adapters/ntfy.py` 가 같은 이유로 쓰는 것과 같다 —
발송 지연이 감지 루프를 막지 않게 하는 것이 목적이다.

## 큐가 차면 signal 부터 버린다

우선순위는 **presence > telemetry > signal** 이다.
- `presence` 는 4Hz 재실 상태로, 잃으면 클라우드의 재실 이력에 구멍이 난다.
- `signal` 은 낙상 추론용이고 늦으면 어차피 가치가 없다(QoS 0 인 이유와 같다).
따라서 압박 상황에서 가장 먼저 희생되는 것이 signal 이어야 한다. 반대로 하면
네트워크가 느릴 때 "사람이 있는지"조차 모르게 된다.

버린 개수는 `status()` 로 나가고 텔레메트리에 실린다 — 조용히 버리지 않는다.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("mqtt.publisher")

RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 30.0


@dataclass(frozen=True)
class Outgoing:
    topic: str
    payload: bytes
    qos: int
    kind: str  # presence | telemetry | signal | ack — 폐기 우선순위 판단용


class MqttPublisher:
    """아웃바운드 발행 스레드. `command.CommandHandler` 와 같은 클라이언트를 공유한다."""

    def __init__(self, config: Any, client_id: str) -> None:
        self.config = config
        self.client_id = client_id
        self._queue: queue.Queue[Outgoing] = queue.Queue(maxsize=config.queue_max)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._lock = threading.Lock()
        self._connected = False
        self._published = 0
        self._dropped = 0
        self._errors = 0
        self._last_error: str | None = None
        #: cmd 구독을 붙일 훅. command.py 가 연결 직후 등록한다.
        self.on_connect_hook = None

    # ── 수명 ────────────────────────────────────────────────────────
    def start(self) -> None:
        import paho.mqtt.client as mqtt

        cfg = self.config
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        if cfg.username:
            client.username_pw_set(cfg.username, cfg.password or None)
        if cfg.tls:
            client.tls_set(
                ca_certs=cfg.ca_cert or None,
                certfile=cfg.client_cert or None,
                keyfile=cfg.client_key or None,
            )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        # paho 가 재연결을 지수 백오프로 처리한다 — 직접 루프를 돌리지 않는다.
        client.reconnect_delay_set(min_delay=int(RECONNECT_MIN_S), max_delay=int(RECONNECT_MAX_S))
        self._client = client

        log.info("mqtt connect %s:%s tls=%s", cfg.broker_host, cfg.broker_port, cfg.tls)
        client.connect_async(cfg.broker_host, cfg.broker_port, keepalive=cfg.keepalive_s)
        client.loop_start()  # paho 내부 네트워크 스레드

        self._thread = threading.Thread(target=self._drain, daemon=True, name="mqtt-publisher")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # 종료 경로에서 예외를 삼킨다
                log.debug("mqtt disconnect 중 예외", exc_info=True)

    # ── 발행 ────────────────────────────────────────────────────────
    def enqueue(self, item: Outgoing) -> bool:
        """큐에 넣고 **즉시** 반환한다. 넣지 못하면 False.

        큐가 차면 signal 을 하나 버려 자리를 만든다. 버릴 signal 이 없으면(전부
        presence/telemetry) 새 항목이 signal 인 경우에 한해 그것을 버린다.
        """
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            pass

        if self._evict_one_signal():
            try:
                self._queue.put_nowait(item)
                return True
            except queue.Full:
                pass

        with self._lock:
            self._dropped += 1
            self._last_error = f"큐 포화 — {item.kind} 폐기"
        log.warning("발행 큐 포화, %s 폐기 (누적 %d)", item.kind, self._dropped)
        return False

    def _evict_one_signal(self) -> bool:
        """큐를 훑어 가장 오래된 signal 하나를 버린다. 순서는 유지한다."""
        kept: list[Outgoing] = []
        evicted = False
        try:
            while True:
                item = self._queue.get_nowait()
                if not evicted and item.kind == "signal":
                    evicted = True
                    with self._lock:
                        self._dropped += 1
                    continue
                kept.append(item)
        except queue.Empty:
            pass
        for item in kept:
            try:
                self._queue.put_nowait(item)
            except queue.Full:  # 이론상 도달 불가 — 뺀 것보다 많이 넣지 않는다
                with self._lock:
                    self._dropped += 1
        return evicted

    def _drain(self) -> None:
        while not self._stop.is_set():
            # 연결 전에는 **큐에서 꺼내지 않는다.** `connect_async` 는 비동기라
            # 기동 직후 잠깐 미연결 상태이고, 그때 꺼내면 rc=4(NO_CONN)로 그냥 유실된다.
            # 꺼내지 않으면 큐에 쌓이고, 넘치면 enqueue 의 정책대로 signal 부터 밀려난다 —
            # 즉 단절 구간에서도 presence 는 살아남는다.
            if not self.connected:
                time.sleep(0.05)
                continue
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._publish(item)

    def _publish(self, item: Outgoing) -> None:
        client = self._client
        if client is None:
            return
        try:
            info = client.publish(item.topic, item.payload, qos=item.qos)
            if info.rc != 0:
                raise RuntimeError(f"publish rc={info.rc}")
            with self._lock:
                self._published += 1
        except Exception as exc:
            with self._lock:
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("발행 실패 %s: %s", item.topic, exc)

    # ── 콜백 ────────────────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        ok = getattr(reason_code, "is_failure", None) is False or reason_code == 0
        with self._lock:
            self._connected = bool(ok)
        if ok:
            log.info("mqtt 연결됨")
            if self.on_connect_hook is not None:
                self.on_connect_hook(client)
        else:
            log.warning("mqtt 연결 거부: %s", reason_code)
            with self._lock:
                self._last_error = f"connect refused: {reason_code}"

    def _on_disconnect(self, client, userdata, *args) -> None:
        with self._lock:
            self._connected = False
        log.warning("mqtt 연결 끊김 — paho 가 재연결을 시도한다")

    # ── 관측 ────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "published": self._published,
                "dropped": self._dropped,
                "errors": self._errors,
                "queued": self._queue.qsize(),
                "last_error": self._last_error,
            }


class NullPublisher:
    """MQTT 없이 전 배선을 돌리기 위한 대체품 (개발·테스트).

    브로커가 없어도 엣지 파이프라인 전체(수집 → 재실 → 게이팅 → 피처)를 검증할 수 있어야
    한다. `--no-mqtt` 로 선택한다.
    """

    def __init__(self, echo: bool = False) -> None:
        self.echo = echo
        self.sent: list[Outgoing] = []
        self.on_connect_hook = None
        self._t0 = time.monotonic()

    def start(self) -> None:
        log.info("MQTT 비활성 (NullPublisher) — 발행 내용을 버린다")

    def stop(self, timeout: float = 0.0) -> None:
        del timeout

    def enqueue(self, item: Outgoing) -> bool:
        self.sent.append(item)
        if self.echo:
            log.info("[null] %s (%dB)", item.topic, len(item.payload))
        return True

    @property
    def connected(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {
            "connected": False,
            "published": len(self.sent),
            "dropped": 0,
            "errors": 0,
            "queued": 0,
            "last_error": None,
        }
