"""인제스트 설정 — 환경변수. 값이 없으면 해당 갈래를 **끄고** 기동한다.

브로커나 Kafka 가 없다고 API 가 못 뜨면 안 된다. CRUD 는 실시간 경로와 무관하게 동작해야
하고, 실제로 AWS 에는 아직 Kafka·MQTT 인스턴스가 없다(M4에서 붙인다). 그래서 `enabled` 가
설정 유무로 결정된다 — `routers/health.py:70-78` 이 `TSDB_DSN`/`KAFKA_BOOTSTRAP` 을 다루는
방식과 같은 규칙이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class IngestSettings:
    # ── MQTT (엣지 업링크 수신) ─────────────────────────────────────
    mqtt_host: str
    mqtt_port: int
    mqtt_tls: bool
    mqtt_username: str
    mqtt_password: str
    mqtt_ca_cert: str

    # ── Kafka ──────────────────────────────────────────────────────
    kafka_bootstrap: str
    kafka_group: str

    # ── TimescaleDB (presence_samples 전용, DATABASE_URL 과 **다른 인스턴스**) ──
    tsdb_dsn: str
    presence_batch_size: int
    presence_flush_interval_s: float

    #: 텔레메트리가 이만큼 끊기면 `Device.online=False`. 엣지 telemetry 는 1Hz(퇴실 5Hz)라
    #: 15초면 여유가 충분하면서도 화면이 너무 늦게 반응하지 않는다.
    offline_after_s: float
    fall_cooldown_seconds: float
    local_edge_token: str = ""

    @classmethod
    def from_env(cls) -> "IngestSettings":
        return cls(
            mqtt_host=os.environ.get("MQTT_HOST", ""),
            mqtt_port=_int("MQTT_PORT", 8883),
            mqtt_tls=os.environ.get("MQTT_TLS", "1") == "1",
            mqtt_username=os.environ.get("MQTT_USERNAME", ""),
            mqtt_password=os.environ.get("MQTT_PASSWORD", ""),
            mqtt_ca_cert=os.environ.get("MQTT_CA_CERT", ""),
            kafka_bootstrap=os.environ.get("KAFKA_BOOTSTRAP", ""),
            kafka_group=os.environ.get("KAFKA_GROUP", "wifiguard-ingest"),
            tsdb_dsn=os.environ.get("TSDB_DSN", ""),
            presence_batch_size=_int("PRESENCE_BATCH_SIZE", 200),
            presence_flush_interval_s=_float("PRESENCE_FLUSH_INTERVAL_S", 1.0),
            offline_after_s=_float("DEVICE_OFFLINE_AFTER_S", 15.0),
            fall_cooldown_seconds=_float("FALL_COOLDOWN_SECONDS", 10.0),
            local_edge_token=os.environ.get("LOCAL_EDGE_TOKEN", ""),
        )

    # ── 어느 갈래가 켜지는가 ────────────────────────────────────────
    @property
    def mqtt_enabled(self) -> bool:
        return bool(self.mqtt_host)

    @property
    def kafka_enabled(self) -> bool:
        return bool(self.kafka_bootstrap)

    @property
    def presence_persist_enabled(self) -> bool:
        return bool(self.tsdb_dsn)

    @property
    def any_enabled(self) -> bool:
        return self.mqtt_enabled or self.kafka_enabled or self.local_enabled

    @property
    def local_enabled(self) -> bool:
        return bool(self.local_edge_token)

    def describe(self) -> str:
        def mark(on: bool) -> str:
            return "on" if on else "off"

        return (
            f"mqtt={mark(self.mqtt_enabled)}({self.mqtt_host or '-'}:{self.mqtt_port}) "
            f"kafka={mark(self.kafka_enabled)}({self.kafka_bootstrap or '-'}) "
            f"tsdb={mark(self.presence_persist_enabled)}"
            f" local={mark(self.local_enabled)}"
        )


settings = IngestSettings.from_env()
