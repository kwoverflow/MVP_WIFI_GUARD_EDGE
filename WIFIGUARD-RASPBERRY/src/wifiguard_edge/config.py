"""설정 로딩 — `config/default.toml` + `config/device.toml` → dataclass.

## dataclass 가 SSOT 이고 TOML 은 오버라이드다

값의 출처는 파이썬 dataclass 다. TOML 은 그 기본값을 재현하고 기기별로 덮어쓸 뿐이다.
둘이 어긋나면 "어느 쪽이 진짜인가"를 매번 추적해야 하므로, `tests/test_config_parity.py`
가 `default.toml` 의 모든 값이 dataclass 기본값과 **일치하는지** 단언한다.

## 모르는 키는 조용히 무시하지 않는다

오타 하나가 며칠 뒤 "왜 이 설정이 안 먹지"로 돌아온다. 알 수 없는 섹션·키는
`ConfigError` 로 기동을 막는다. 고빈도 엣지 프로세스에서 조용한 무시는 최악이다.
"""

from __future__ import annotations

import tomllib
from dataclasses import MISSING, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

from .features.realtime import FeatureConfig
from .presence.config import PresenceConfig

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class ConfigError(Exception):
    """설정 파일이 잘못됐다. 기동을 중단시킨다."""


# ── 신규 섹션 dataclass (기존 PresenceConfig·FeatureConfig 는 그대로 재사용) ──
@dataclass
class CalibrationConfig:
    """필드명이 `calibration.onboarding.run_calibration()` 의 키워드 인자와 같아야 한다.

    그래야 `run_calibration(monitor, cfg, calib, executor, **asdict(calibration_config))`
    로 그대로 넘길 수 있다. 예전 TOML 은 `leaving_s`/`measuring_s` 라는 다른 이름을 써서
    어느 쪽도 상대를 모르는 상태였다.
    """

    leave_wait_s: float = 30.0
    silence_confirm_s: float = 0.2
    silence_timeout_s: float = 3.0
    resume_timeout_s: float = 20.0
    baseline_window_s: float = 30.0
    poll_interval_s: float = 0.05
    k_mv: float = 2.0
    mv_floor: float = 0.3
    wander_baseline_floor: float = 0.05


@dataclass
class GatingConfig:
    """업로드 게이트. 사람이 없는 구간의 신호는 올리지 않는다.

    게이트가 닫혀 있었다는 사실 자체는 telemetry 로 **항상** 보고한다 — 무증상 침묵과
    "정상적으로 조용함"을 구별할 수 있어야 하기 때문이다.
    """

    signal_publish_hz: float = 4.0
    telemetry_interval_s: float = 1.0
    telemetry_absent_interval_s: float = 5.0
    #: ABSENT 로 바뀐 뒤에도 이만큼 더 올린다. 낙상 직후 사람이 쓰러져 움직이지 않으면
    #: 재실이 ABSENT 로 떨어지는데, 바로 게이트를 닫으면 그 구간을 잃는다.
    linger_after_absent_s: float = 10.0
    #: 재실 미상(아직 한 틱도 안 돎)일 때 올릴 것인가. 기본은 올린다 — 초기 구간을
    #: 통째로 잃는 것보다 낫고, gate_reason 에 "forced" 로 남는다.
    publish_when_unknown: bool = True


@dataclass
class MqttConfig:
    broker_host: str = "localhost"
    broker_port: int = 8883
    tls: bool = True
    ca_cert: str = ""
    client_cert: str = ""
    client_key: str = ""
    username: str = ""
    password: str = ""
    keepalive_s: int = 30
    qos_presence: int = 1
    qos_telemetry: int = 1
    qos_signal: int = 0  # 최신성 우선 — 늦은 신호는 가치가 없다
    qos_cmd_ack: int = 1
    #: 발행 큐 상한. 넘치면 오래된 signal 부터 버린다 — 발행 지연이 감지 루프를 막으면 안 된다.
    queue_max: int = 256


@dataclass
class TransportConfig:
    kind: str = "serial"  # serial | replay | spi
    serial_port: str = ""
    baudrate: int = 2_000_000
    replay_source: str = "synthetic"
    replay_speed: float = 1.0
    spi_bus: int = 0
    spi_device: int = 0
    max_speed_hz: int = 6_000_000
    gpio_data_ready: int = 25
    ready_gpio_chip: str = "/dev/gpiochip0"
    ready_bias: str = "pull-down"
    spi_batch_frames: int = 8
    spi_transfer_api: str = "direct-ioctl"
    spi_protocol_resync_seconds: float = 0.05


@dataclass
class DeviceConfig:
    tenant_id: str = ""
    device_id: str = ""


@dataclass
class EdgeConfig:
    device: DeviceConfig
    transport: TransportConfig
    presence: PresenceConfig
    features: FeatureConfig
    calibration: CalibrationConfig
    gating: GatingConfig
    mqtt: MqttConfig

    def validate(self) -> None:
        """기동 전 검증. 여기서 막는 편이 4Hz 로 실패하는 것보다 낫다."""
        if self.transport.kind not in ("serial", "replay", "spi"):
            raise ConfigError(f"[transport] kind 는 serial|replay|spi 중 하나여야 한다: {self.transport.kind!r}")
        if self.transport.kind == "serial" and self.transport.baudrate <= 0:
            raise ConfigError("[transport] baudrate 가 양수가 아니다")
        if self.transport.kind == "spi":
            if not 1_000_000 <= self.transport.max_speed_hz <= 50_000_000:
                raise ConfigError("[transport] max_speed_hz 는 1~50MHz 범위여야 한다")
            if self.transport.spi_batch_frames not in (1, 2, 4, 8):
                raise ConfigError("[transport] spi_batch_frames 는 1|2|4|8 중 하나여야 한다")
            if self.transport.spi_transfer_api != "direct-ioctl":
                raise ConfigError("[transport] 현재 지원하는 SPI API는 direct-ioctl뿐이다")
            if self.transport.ready_bias not in ("as-is", "disabled", "pull-down", "pull-up"):
                raise ConfigError("[transport] ready_bias 값이 올바르지 않다")
        if self.gating.signal_publish_hz <= 0:
            raise ConfigError("[gating] signal_publish_hz 가 양수가 아니다")
        for name in ("tenant_id", "device_id"):
            if not getattr(self.device, name):
                raise ConfigError(
                    f"[device] {name} 가 비어 있다. config/device.toml 에 지정할 것 "
                    "(백엔드 GET /api/v1/devices 의 mqttTopic 에서 가져온다)"
                )


_SECTIONS: dict[str, type] = {
    "device": DeviceConfig,
    "transport": TransportConfig,
    "presence": PresenceConfig,
    "features": FeatureConfig,
    "calibration": CalibrationConfig,
    "gating": GatingConfig,
    "mqtt": MqttConfig,
}


def _build(section: str, cls: type, values: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(values) - known
    if unknown:
        raise ConfigError(
            f"[{section}] 에 알 수 없는 키: {sorted(unknown)}. "
            f"사용 가능한 키: {sorted(known)}"
        )
    coerced: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in values:
            continue
        raw = values[f.name]
        # TOML 은 int/float 을 구분하므로 float 필드에 정수가 오면 맞춰 준다 (예: 3 → 3.0).
        if f.type in ("float", float) and isinstance(raw, int) and not isinstance(raw, bool):
            raw = float(raw)
        elif f.type in ("tuple[int, ...]",) and isinstance(raw, list):
            raw = tuple(raw)
        coerced[f.name] = raw
    return cls(**coerced)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"설정 파일이 없다: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} 파싱 실패: {exc}") from exc


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """섹션 단위 얕은 병합. 섹션 안의 키는 개별로 덮어쓴다."""
    merged = {k: dict(v) for k, v in base.items()}
    for section, values in override.items():
        if not isinstance(values, dict):
            raise ConfigError(f"최상위에 섹션이 아닌 키가 있다: {section!r}")
        merged.setdefault(section, {}).update(values)
    return merged


def load_config(
    config_dir: Path | None = None,
    *,
    device_file: str = "device.toml",
    require_device: bool = True,
) -> EdgeConfig:
    """`default.toml` 을 읽고 `device.toml` 로 덮어쓴 뒤 dataclass 로 만든다.

    `require_device=False` 면 device.toml 이 없어도 기본값으로 진행한다(테스트·벤치용).
    """
    cfg_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    raw = _read_toml(cfg_dir / "default.toml")

    device_path = cfg_dir / device_file
    if device_path.exists():
        raw = _merge(raw, _read_toml(device_path))
    elif require_device:
        raise ConfigError(
            f"{device_path} 가 없다. `cp config/device.toml.example config/device.toml` 후 "
            "tenant_id·device_id 를 채울 것."
        )

    unknown_sections = set(raw) - set(_SECTIONS)
    if unknown_sections:
        raise ConfigError(f"알 수 없는 섹션: {sorted(unknown_sections)}")

    built = {name: _build(name, cls, raw.get(name, {})) for name, cls in _SECTIONS.items()}
    config = EdgeConfig(**built)
    config.validate()
    return config


def dataclass_defaults(cls: type) -> dict[str, Any]:
    """dataclass 의 기본값 dict. 파리티 테스트가 TOML 과 대조하는 기준."""
    if not is_dataclass(cls):
        raise TypeError(f"{cls} 는 dataclass 가 아니다")
    out: dict[str, Any] = {}
    for f in fields(cls):
        if f.default is not MISSING:
            out[f.name] = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            out[f.name] = f.default_factory()  # type: ignore[misc]
    return out
