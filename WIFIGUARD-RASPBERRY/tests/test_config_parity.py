"""`config/default.toml` 이 dataclass 기본값을 정확히 재현하는지 검증한다.

dataclass 가 SSOT 이고 TOML 은 그 사본이다. 둘이 갈라지면 "어느 쪽이 진짜 기본값인가"를
매번 코드로 확인해야 하고, 그 혼동이 실제로 있었다 — 예전 `[calibration]` 은
`leaving_s`/`measuring_s` 라는 이름을 썼는데 `run_calibration()` 의 인자는
`leave_wait_s`/`baseline_window_s` 였다. 어느 쪽도 상대를 몰랐고, 그 파일을 읽는 코드가
아예 없어서 아무도 눈치채지 못했다.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from wifiguard_edge.config import (
    DEFAULT_CONFIG_DIR,
    CalibrationConfig,
    ConfigError,
    DeviceConfig,
    GatingConfig,
    MqttConfig,
    TransportConfig,
    dataclass_defaults,
    load_config,
)
from wifiguard_edge.features.realtime import FeatureConfig
from wifiguard_edge.presence.config import PresenceConfig

SECTIONS = {
    "device": DeviceConfig,
    "transport": TransportConfig,
    "presence": PresenceConfig,
    "features": FeatureConfig,
    "calibration": CalibrationConfig,
    "gating": GatingConfig,
    "mqtt": MqttConfig,
}


@pytest.fixture(scope="module")
def toml_raw() -> dict:
    with (DEFAULT_CONFIG_DIR / "default.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_no_extra_or_missing_sections(toml_raw):
    assert set(toml_raw) == set(SECTIONS)


@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_section_keys_match_dataclass_fields(toml_raw, section):
    """키 집합이 정확히 같아야 한다 — 한쪽만 늘어도 실패."""
    assert set(toml_raw[section]) == set(dataclass_defaults(SECTIONS[section]))


@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_section_values_match_dataclass_defaults(toml_raw, section):
    defaults = dataclass_defaults(SECTIONS[section])
    for key, toml_value in toml_raw[section].items():
        expected = defaults[key]
        if isinstance(expected, tuple):
            toml_value = tuple(toml_value)
        assert toml_value == pytest.approx(expected) if isinstance(expected, float) else toml_value == expected, (
            f"[{section}] {key}: toml={toml_value!r} != dataclass={expected!r}"
        )


def test_calibration_keys_match_run_calibration_signature():
    """`run_calibration(**asdict(cfg.calibration))` 이 성립해야 한다."""
    import inspect

    from wifiguard_edge.calibration.onboarding import run_calibration

    kwonly = {
        name
        for name, p in inspect.signature(run_calibration).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert set(dataclass_defaults(CalibrationConfig)) == kwonly


def test_baud_default_matches_serial_reader():
    """`[transport] baudrate` 와 `SerialReader.DEFAULT_BAUD` 가 어긋나면 프레임이 파싱되지 않는다."""
    from wifiguard_edge.csi.serial_reader import DEFAULT_BAUD

    assert TransportConfig().baudrate == DEFAULT_BAUD == 2_000_000


# ── 로더 동작 ───────────────────────────────────────────────────────
def _write(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / name).write_text(body, encoding="utf-8")


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    _write(tmp_path, "default.toml", (DEFAULT_CONFIG_DIR / "default.toml").read_text(encoding="utf-8"))
    return tmp_path


def test_device_toml_overrides_default(cfg_dir):
    _write(
        cfg_dir,
        "device.toml",
        '[device]\ntenant_id = "home-x"\ndevice_id = "d1"\n[transport]\nkind = "replay"\n',
    )
    cfg = load_config(cfg_dir)
    assert cfg.transport.kind == "replay"
    assert cfg.device.tenant_id == "home-x"
    # 덮어쓰지 않은 키는 default 를 유지한다 (섹션 통째 교체가 아니라 키 단위 병합)
    assert cfg.transport.baudrate == 2_000_000
    assert cfg.presence.presence_mv_threshold == 2.0


def test_unknown_key_is_rejected(cfg_dir):
    _write(cfg_dir, "device.toml", '[device]\ntenant_id="t"\ndevice_id="d"\n[gating]\nsignal_publish_hzz = 4.0\n')
    with pytest.raises(ConfigError, match="알 수 없는 키"):
        load_config(cfg_dir)


def test_unknown_section_is_rejected(cfg_dir):
    _write(cfg_dir, "device.toml", '[device]\ntenant_id="t"\ndevice_id="d"\n[gatingg]\nx = 1\n')
    with pytest.raises(ConfigError, match="알 수 없는 섹션"):
        load_config(cfg_dir)


def test_missing_device_identity_is_rejected(cfg_dir):
    _write(cfg_dir, "device.toml", '[transport]\nkind = "replay"\n')
    with pytest.raises(ConfigError, match="tenant_id"):
        load_config(cfg_dir)


def test_missing_device_file_is_rejected_by_default(cfg_dir):
    with pytest.raises(ConfigError, match="device.toml"):
        load_config(cfg_dir)


def test_int_is_coerced_to_float_field(cfg_dir):
    """TOML 은 3 과 3.0 을 구분한다. float 필드에 정수를 써도 받아야 한다."""
    _write(cfg_dir, "device.toml", '[device]\ntenant_id="t"\ndevice_id="d"\n[gating]\nsignal_publish_hz = 2\n')
    cfg = load_config(cfg_dir)
    assert isinstance(cfg.gating.signal_publish_hz, float)
    assert cfg.gating.signal_publish_hz == 2.0


def test_spi_kind_builds_wgsp_source_without_opening_hardware(cfg_dir):
    """팩토리는 SPI 장치를 미리 열지 않고 WGSP 소스를 구성한다."""
    from wifiguard_edge.transport.base import create_source
    from wifiguard_edge.transport.wgsp_source import WgspBatch8Source

    _write(cfg_dir, "device.toml", '[device]\ntenant_id="t"\ndevice_id="d"\n[transport]\nkind = "spi"\n')
    cfg = load_config(cfg_dir)
    source = create_source("spi", buffer=None, config=cfg.transport)  # type: ignore[arg-type]
    assert isinstance(source, WgspBatch8Source)
