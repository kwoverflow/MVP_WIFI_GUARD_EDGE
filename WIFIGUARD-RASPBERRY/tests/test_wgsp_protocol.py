from __future__ import annotations

import struct
import zlib
from dataclasses import replace
from typing import Literal, cast

import numpy as np
import pytest
from wifiguard_edge.transport.wgsp_protocol import (
    CRC_STRUCT,
    FLAG_CALIBRATING,
    FLAG_CSI_VALID,
    FLAG_DROPPED_SINCE_PREVIOUS,
    FRAME_SIZE_OFFSET,
    HEADER_STRUCT,
    SPI_MAGIC,
    SpiCsiFrame,
    SpiCsiParser,
    SpiProtocolError,
    decode_spi_csi_frame,
    encode_spi_csi_frame,
    profile_fingerprint,
    spi_frame_size_from_header,
)


def _frame(*, flags: int = FLAG_CSI_VALID) -> SpiCsiFrame:
    iq = np.arange(60, dtype=np.int8).reshape(30, 2)
    return SpiCsiFrame(
        stream_epoch=0x1234,
        frame_seq=91,
        source_timestamp_us=1_234_567,
        source_dropped_total=1,
        queue_dropped_total=2,
        spi_dropped_total=3,
        transport_errors_total=4,
        channel=48,
        bandwidth_mhz=20,
        rssi=-47,
        noise_floor=-93,
        agc_gain=24,
        fft_gain=-2,
        rx_format=4,
        flags=flags,
        profile_fingerprint=profile_fingerprint("esp32c5-he20-5g-ch48-amfall-spi-v1"),
        csi_iq=iq,
    )


@pytest.mark.parametrize("wire_order", ["IQ", "QI"])
def test_spi_round_trip_is_aligned_and_canonical(wire_order: str) -> None:
    expected = _frame()
    transaction = encode_spi_csi_frame(
        expected,
        wire_iq_order=cast(Literal["IQ", "QI"], wire_order),
    )

    assert len(transaction) % 4 == 0
    assert len(transaction) == struct.unpack_from("<I", transaction, FRAME_SIZE_OFFSET)[0]
    actual = decode_spi_csi_frame(transaction)
    assert actual.stream_epoch == expected.stream_epoch
    assert actual.frame_seq == expected.frame_seq
    assert actual.cumulative_loss_total == 10
    assert actual.usable_for_preprocessing is True
    np.testing.assert_array_equal(actual.csi_iq, expected.csi_iq)


def test_diagnostic_calibration_frame_is_decoded_but_not_usable() -> None:
    actual = decode_spi_csi_frame(
        encode_spi_csi_frame(_frame(flags=FLAG_CSI_VALID | FLAG_CALIBRATING))
    )
    assert actual.usable_for_preprocessing is False


def test_explicit_drop_flag_does_not_hide_independent_counters() -> None:
    actual = decode_spi_csi_frame(
        encode_spi_csi_frame(_frame(flags=FLAG_CSI_VALID | FLAG_DROPPED_SINCE_PREVIOUS))
    )
    assert actual.cumulative_loss_total == 10
    assert actual.usable_for_preprocessing is True


@pytest.mark.parametrize("corruption", ["crc", "padding", "size", "magic"])
def test_corruption_fails_closed(corruption: str) -> None:
    frame = _frame()
    if corruption == "padding":
        frame = replace(frame, csi_iq=np.arange(62, dtype=np.int8).reshape(31, 2))
    transaction = bytearray(encode_spi_csi_frame(frame))
    if corruption == "crc":
        transaction[-1] ^= 0xFF
    elif corruption == "padding":
        payload_end = HEADER_STRUCT.size + frame.csi_iq.size
        assert len(transaction) - CRC_STRUCT.size - payload_end > 0
        transaction[payload_end] = 1
        body = transaction[: -CRC_STRUCT.size]
        transaction[-CRC_STRUCT.size :] = CRC_STRUCT.pack(zlib.crc32(body) & 0xFFFF_FFFF)
    elif corruption == "size":
        struct.pack_into("<I", transaction, FRAME_SIZE_OFFSET, len(transaction) + 4)
        body = transaction[: -CRC_STRUCT.size]
        transaction[-CRC_STRUCT.size :] = CRC_STRUCT.pack(zlib.crc32(body) & 0xFFFF_FFFF)
    else:
        transaction[: len(SPI_MAGIC)] = b"NOPE"
        body = transaction[: -CRC_STRUCT.size]
        transaction[-CRC_STRUCT.size :] = CRC_STRUCT.pack(zlib.crc32(body) & 0xFFFF_FFFF)

    with pytest.raises(SpiProtocolError):
        decode_spi_csi_frame(bytes(transaction))


def test_incremental_parser_resynchronizes_and_reports_rejects() -> None:
    reasons: list[str] = []
    first = encode_spi_csi_frame(_frame())
    second_frame = replace(_frame(), frame_seq=92)
    second = encode_spi_csi_frame(second_frame)
    parser = SpiCsiParser(on_reject=reasons.append)

    assert parser.feed(b"junk" + first[:31]) == ()
    frames = parser.feed(first[31:] + second)

    assert [frame.frame_seq for frame in frames] == [91, 92]
    assert "resync" in reasons


def test_parser_accepts_explicit_header_then_body_physical_phases() -> None:
    transaction = encode_spi_csi_frame(_frame(), wire_iq_order="QI")
    parser = SpiCsiParser()

    assert parser.feed(transaction[: HEADER_STRUCT.size]) == ()
    frames = parser.feed(transaction[HEADER_STRUCT.size :])

    assert len(frames) == 1
    assert frames[0].frame_seq == 91
    np.testing.assert_array_equal(frames[0].csi_iq, _frame().csi_iq)


def test_phase_one_header_returns_bounded_logical_frame_size() -> None:
    transaction = encode_spi_csi_frame(_frame())

    assert spi_frame_size_from_header(transaction[: HEADER_STRUCT.size]) == len(transaction)

    corrupted = bytearray(transaction[: HEADER_STRUCT.size])
    struct.pack_into("<I", corrupted, FRAME_SIZE_OFFSET, 8_192)
    with pytest.raises(SpiProtocolError, match="logical frame size"):
        spi_frame_size_from_header(bytes(corrupted))


def test_profile_fingerprint_is_stable_and_rejects_blank() -> None:
    assert profile_fingerprint("profile") == profile_fingerprint(" profile ")
    assert len(profile_fingerprint("profile")) == 8
    with pytest.raises(ValueError, match="blank"):
        profile_fingerprint("  ")
