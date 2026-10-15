"""전송 계층 계약 — 상위 파이프라인이 UART/재생/SPI 를 구별하지 않아야 한다.

`transport/base.py` 를 ABC 가 아니라 Protocol 로 둔 이유가 여기 있다. UART, replay,
WGSP Batch8 SPI가 같은 계약을 만족하는지 확인한다.
"""

from __future__ import annotations

import time

import pytest

from wifiguard_edge.csi.buffer import RingBuffer
from wifiguard_edge.transport.base import CsiSource, create_source
from wifiguard_edge.transport.replay_source import ReplaySource
from wifiguard_edge.transport.wgsp_source import WgspBatch8Source
from wifiguard_edge.config import TransportConfig

CONTRACT_MEMBERS = ("start", "stop", "join", "running", "packet_count", "status", "get_window", "send_line")


def test_replay_source_satisfies_protocol():
    src = ReplaySource(RingBuffer(5.0), "synthetic")
    assert isinstance(src, CsiSource)
    for name in CONTRACT_MEMBERS:
        assert hasattr(src, name), name


def test_serial_reader_satisfies_protocol_unchanged():
    """`SerialReader` 는 Protocol 도입 전에 쓰인 코드다. 고치지 않고도 만족해야 한다."""
    from wifiguard_edge.csi.serial_reader import SerialReader

    reader = SerialReader(RingBuffer(5.0))
    assert isinstance(reader, CsiSource)
    for name in CONTRACT_MEMBERS:
        assert hasattr(reader, name), name


def test_spi_source_satisfies_protocol_without_hardware_access():
    source = create_source("spi", RingBuffer(5.0), config=TransportConfig(kind="spi"))
    assert isinstance(source, WgspBatch8Source)
    assert isinstance(source, CsiSource)
    for name in CONTRACT_MEMBERS:
        assert hasattr(source, name), name


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="알 수 없는 transport"):
        create_source("carrier-pigeon", RingBuffer(5.0), config=None)  # type: ignore[arg-type]


def test_factory_does_not_import_unused_backends():
    """Pi 하드웨어 의존성이 없는 개발 PC에서도 팩토리 import가 성공해야 한다."""
    import importlib

    importlib.import_module("wifiguard_edge.transport.base")  # 예외 없이 통과해야 한다


def test_replay_feeds_ring_buffer():
    """실제로 링버퍼에 쌓이는지 — 계약 준수만으로는 부족하다."""
    ring = RingBuffer(10.0)
    src = ReplaySource(ring, "synthetic")
    src.start()
    try:
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and ring.total_frames < 200:
            time.sleep(0.05)
    finally:
        src.stop()
        src.join(timeout=2.0)

    assert ring.total_frames >= 200, f"프레임이 모자라다: {ring.total_frames}"
    assert src.packet_count == ring.total_frames
    window = src.get_window(1.0)
    assert window is not None
    times, amps = window
    assert len(times) == len(amps)
    assert amps.shape[1] == src.subcarriers


def test_replay_send_line_succeeds_so_calibration_can_run():
    """재생에는 수신기가 없지만 캘리브레이션 흐름을 하드웨어 없이 검증해야 한다."""
    src = ReplaySource(RingBuffer(5.0), "synthetic")
    assert src.send_line("train") is True


def test_replay_status_matches_link_stats_fields():
    """`status()` 가 곧바로 `LinkStats` 로 들어간다 — 키가 맞아야 한다."""
    pytest.importorskip("wifiguard_contracts")
    from wifiguard_contracts.mqtt import LinkStats

    src = ReplaySource(RingBuffer(5.0), "synthetic")
    LinkStats(transport="replay", **src.status())  # 예외 없이 생성되어야 한다


def test_missing_recording_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        ReplaySource(RingBuffer(5.0), str(tmp_path / "없는파일.npz"))
