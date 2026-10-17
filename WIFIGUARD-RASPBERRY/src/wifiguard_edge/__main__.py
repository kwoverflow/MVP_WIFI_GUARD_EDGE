"""엣지 엔트리포인트 — `python -m wifiguard_edge` 또는 `wifiguard-edge`.

    수신기 ──► RingBuffer ──┬─► PresenceLoop (0.25s, 상시)  ──► presence 4Hz
                            └─► FeatureLoop  (0.25s, 게이트) ──► signal 4Hz
                                                             └─► telemetry 1Hz

## 스레드 기동·정지 순서

기동: 전송 → 재실 → 피처 → 텔레메트리. 소비자보다 생산자를 먼저 띄운다.
정지: **역순.** 링버퍼를 읽는 쪽을 먼저 멈춰야 종료 중 빈 윈도우로 인한 경고가 안 뜬다.
MQTT 는 마지막에 닫는다 — 종료 직전 텔레메트리를 내보낼 기회를 주기 위해서다.

## 재실감지는 클라우드와 무관하게 돈다

MQTT 가 끊겨도, 브로커 설정이 없어도(`--no-mqtt`) `PresenceLoop` 는 계속 돈다.
"네트워크가 끊겨도 지금 사람이 있는가는 유지된다"는 것이 이 레포의 책임 경계다(G7).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from .config import ConfigError, EdgeConfig, load_config
from .csi.buffer import RingBuffer
from .feature_loop import FeatureLoop
from .gating import SignalGate
from .presence_loop import PresenceLoop
from .transport.base import create_source

log = logging.getLogger("wifiguard_edge")

EDGE_VERSION = "0.1.0"
HEALTH_REFRESH_SECONDS = 5.0


class EdgeApp:
    """스레드 4개(전송·재실·피처·텔레메트리)와 MQTT 발행자의 수명을 관리한다."""

    def __init__(self, config: EdgeConfig, *, use_mqtt: bool = True, echo: bool = False) -> None:
        self.config = config
        self.use_mqtt = use_mqtt
        self._t0 = time.monotonic()
        self._stop = threading.Event()
        self._seq = 0
        self._seq_lock = threading.Lock()
        health_file = os.getenv("HEALTH_FILE", "").strip()
        self._health_file = Path(health_file) if health_file else None

        self.ring = RingBuffer(max_seconds=30.0)
        self.gate = SignalGate(
            linger_after_absent_s=config.gating.linger_after_absent_s,
            publish_when_unknown=config.gating.publish_when_unknown,
        )
        self.source = create_source(config.transport.kind, self.ring, config.transport)  # type: ignore[arg-type]
        self.presence = PresenceLoop(self.ring, config.presence)
        self.features = FeatureLoop(
            self.ring,
            config.features,
            self.gate,
            sink=self._on_signal,
            presence_state=self._presence_state,
        )

        self.publisher = self._make_publisher(echo=echo)
        self.commands = self._make_commands()
        self._telemetry_thread = threading.Thread(
            target=self._telemetry_loop, daemon=True, name="telemetry"
        )

    # ── 배선 ────────────────────────────────────────────────────────
    @property
    def device_id(self) -> UUID:
        return UUID(self.config.device.device_id)

    @property
    def tenant_id(self) -> str:
        return self.config.device.tenant_id

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _presence_state(self) -> str | None:
        return self.presence.presence_payload().get("state")

    def _make_publisher(self, *, echo: bool) -> Any:
        from .mqtt.publisher import MqttPublisher, NullPublisher

        if not self.use_mqtt:
            return NullPublisher(echo=echo)
        return MqttPublisher(self.config.mqtt, client_id=f"wifiguard-edge-{self.config.device.device_id}")

    def _make_commands(self) -> Any:
        from .mqtt.command import CommandHandler, make_ping_handler

        handler = CommandHandler(
            self.publisher,
            device_id=self.device_id,
            tenant_id=self.tenant_id,
            qos=self.config.mqtt.qos_cmd_ack,
        )
        handler.register("ping", make_ping_handler())
        # calibrate / set_config / set_mode 는 M1 범위 밖 — 등록되지 않은 명령은
        # CommandHandler 가 error ack 로 답한다(무응답으로 두지 않는다).
        self.publisher.on_connect_hook = handler.attach
        return handler

    # ── 발행 ────────────────────────────────────────────────────────
    def _on_signal(self, window, decision) -> None:
        """FeatureLoop 콜백. **즉시 반환해야 한다** — 여기서 막으면 윈도우를 놓친다."""
        from wifiguard_contracts import topics

        from .mqtt import codec
        from .mqtt.publisher import Outgoing

        msg = codec.build_signal(
            window,
            decision,
            device_id=self.device_id,
            tenant_id=self.tenant_id,
            seq=self._next_seq(),
            presence_state=self._presence_state(),
        )
        self.publisher.enqueue(
            Outgoing(
                topic=topics.leaf_topic(self.tenant_id, self.device_id, "signal"),
                payload=codec.to_bytes(msg),
                qos=self.config.mqtt.qos_signal,
                kind="signal",
            )
        )

    def _publish_presence(self) -> None:
        from wifiguard_contracts import topics

        from .mqtt import codec
        from .mqtt.publisher import Outgoing

        payload = self.presence.presence_payload()
        self.gate.note_presence(payload.get("state"))
        msg = codec.build_presence(
            payload,
            device_id=self.device_id,
            tenant_id=self.tenant_id,
            seq=self._next_seq(),
            tick_uptime_s=time.monotonic() - self._t0,
        )
        if msg is None:  # 아직 미상 — present/absent 어느 쪽으로도 단정하지 않는다
            return
        self.publisher.enqueue(
            Outgoing(
                topic=topics.leaf_topic(self.tenant_id, self.device_id, "presence"),
                payload=codec.to_bytes(msg),
                qos=self.config.mqtt.qos_presence,
                kind="presence",
            )
        )

    def _publish_telemetry(self) -> None:
        from wifiguard_contracts import topics

        from .mqtt import codec
        from .mqtt.publisher import Outgoing

        published, gated = self.gate.counters()
        msg = codec.build_telemetry(
            device_id=self.device_id,
            tenant_id=self.tenant_id,
            seq=self._next_seq(),
            link=self.source.status(),
            transport=self.config.transport.kind,
            presence_loop=_loop_stats(self.presence.status()),
            feature_loop=_loop_stats(self.features.status()),
            gate_open=self.gate.is_open,
            signals_published=published,
            signals_gated=gated,
            uptime_s=time.monotonic() - self._t0,
            edge_version=EDGE_VERSION,
        )
        self.publisher.enqueue(
            Outgoing(
                topic=topics.leaf_topic(self.tenant_id, self.device_id, "telemetry"),
                payload=codec.to_bytes(msg),
                qos=self.config.mqtt.qos_telemetry,
                kind="telemetry",
            )
        )

    def _telemetry_loop(self) -> None:
        """presence 4Hz + telemetry 1Hz. 두 주기를 한 스레드에서 돌린다.

        발행 자체는 큐 넣기라 비용이 없으므로 스레드를 더 늘릴 이유가 없다.
        """
        presence_period = 1.0 / max(self.config.gating.signal_publish_hz, 0.1)
        next_presence = next_telemetry = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_presence:
                next_presence = max(next_presence + presence_period, now)
                try:
                    self._publish_presence()
                except Exception:
                    log.exception("presence 발행 실패")
            if now >= next_telemetry:
                absent = not self.gate.is_open
                interval = (
                    self.config.gating.telemetry_absent_interval_s
                    if absent
                    else self.config.gating.telemetry_interval_s
                )
                next_telemetry = max(next_telemetry + interval, now)
                try:
                    self._publish_telemetry()
                except Exception:
                    log.exception("telemetry 발행 실패")
            time.sleep(0.02)

    # ── 수명 ────────────────────────────────────────────────────────
    def start(self) -> None:
        log.info(
            "엣지 기동 device=%s tenant=%s transport=%s mqtt=%s",
            self.config.device.device_id, self.tenant_id,
            self.config.transport.kind, "on" if self.use_mqtt else "off",
        )
        self.publisher.start()
        self.source.start()      # 생산자 먼저
        self.presence.start()
        self.features.start()
        self._telemetry_thread.start()

    def stop(self) -> None:
        """정지는 기동의 역순 — 소비자를 먼저 멈춘다."""
        log.info("엣지 정지 중…")
        self._stop.set()
        self._telemetry_thread.join(timeout=2.0)
        self.features.stop()
        self.presence.stop()
        self.features.join(timeout=2.0)
        self.presence.join(timeout=2.0)
        self.source.stop()
        self.source.join(timeout=2.0)
        self.publisher.stop()    # 마지막 — 종료 직전 큐를 비울 기회를 준다
        log.info("정지 완료: %s", self.publisher.status())

    def _refresh_health(self) -> None:
        """Refresh the inherited container heartbeat only while core loops live."""
        if self._health_file is None:
            return
        healthy = (
            self.source.running
            and self.presence.is_alive()
            and self.features.is_alive()
            and self._telemetry_thread.is_alive()
        )
        if healthy:
            self._health_file.parent.mkdir(parents=True, exist_ok=True)
            self._health_file.touch()
        else:
            self._health_file.unlink(missing_ok=True)

    def _clear_health(self) -> None:
        if self._health_file is not None:
            self._health_file.unlink(missing_ok=True)

    def run_forever(self) -> None:
        self.start()
        next_health = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_health:
                    self._refresh_health()
                    next_health = now + HEALTH_REFRESH_SECONDS
                time.sleep(0.2)
        finally:
            try:
                self.stop()
            finally:
                self._clear_health()


def _loop_stats(status: dict[str, Any]) -> dict[str, Any]:
    """루프 `status()` 에서 `LoopStats` 4필드만 뽑는다 (재실은 payload 11필드가 섞여 있다)."""
    return {k: status[k] for k in ("enabled", "tick_count", "skip_count", "last_error")}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wifiguard-edge", description=__doc__.split("\n")[0])
    ap.add_argument("--config-dir", type=Path, default=None, help="기본: 레포의 config/")
    ap.add_argument("--transport", choices=("serial", "replay", "spi"), help="config 의 [transport] kind 를 덮어쓴다")
    ap.add_argument("--replay-source", help='"synthetic" 또는 recording(.npz) 경로')
    ap.add_argument("--no-mqtt", action="store_true", help="브로커 없이 파이프라인만 돌린다")
    ap.add_argument("--echo", action="store_true", help="--no-mqtt 일 때 발행 내용을 로그로")
    ap.add_argument("--duration", type=float, default=None, help="N초 후 자동 종료 (스모크 테스트용)")
    ap.add_argument("--log-level", default="INFO")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
    )

    try:
        config = load_config(args.config_dir, require_device=True)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    if args.transport:
        config.transport.kind = args.transport
    if args.replay_source:
        config.transport.replay_source = args.replay_source
    try:
        config.validate()
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    app = EdgeApp(config, use_mqtt=not args.no_mqtt, echo=args.echo)

    def _handle(signum, frame):
        del signum, frame
        app._stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):  # 비메인 스레드 등
            pass

    if args.duration:
        threading.Timer(args.duration, app._stop.set).start()

    app.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
