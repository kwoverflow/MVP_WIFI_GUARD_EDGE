"""Versioned ESP32-C5 to Raspberry Pi SPI CSI wire contract.

This module deliberately contains no ``spidev`` or GPIO access.  It defines the
bounded, little-endian transaction that the future C5 SPI-slave firmware and Pi
SPI-master adapter must share, so both sides can be implemented and tested
before hardware is connected.

The final CRC covers the header, payload, and zero padding.  A transaction is
always four-byte aligned for DMA use.  CSI samples leave this boundary in
canonical signed-int8 ``[I, Q]`` order, independent of the on-wire IQ order.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

SPI_MAGIC = b"WGSP"
SPI_VERSION = 1
SPI_KIND_CSI = 1

FLAG_CSI_VALID = 1 << 0
FLAG_FIRST_WORD_INVALID = 1 << 1
FLAG_TRUNCATED = 1 << 2
FLAG_CALIBRATING = 1 << 3
FLAG_DROPPED_SINCE_PREVIOUS = 1 << 4
KNOWN_FLAGS = (
    FLAG_CSI_VALID
    | FLAG_FIRST_WORD_INVALID
    | FLAG_TRUNCATED
    | FLAG_CALIBRATING
    | FLAG_DROPPED_SINCE_PREVIOUS
)
UNUSABLE_FLAGS = FLAG_FIRST_WORD_INVALID | FLAG_TRUNCATED | FLAG_CALIBRATING

IQ_ORDER_IQ = 0
IQ_ORDER_QI = 1
RX_FORMAT_HE_SU = 4

MAX_SAFE_FRAME_SEQ = (1 << 53) - 1
MAX_SOURCE_TIMESTAMP_US = (1 << 63) - 1
MAX_COUNTER = (1 << 32) - 1
MAX_SPI_FRAME_BYTES = 4_096
AMFALL_SPI_FRAME_BYTES = 576

# 80-byte, explicitly sized header.  Keep this in lock-step with the staged C
# header documented in docs/AMFALL_SPI_PREPARATION.md.
HEADER_STRUCT = struct.Struct("<4sBBHHHIQQQIIIIHHHHbbBbBBH8s")
CRC_STRUCT = struct.Struct("<I")
FRAME_SIZE_OFFSET = 12


class SpiProtocolError(ValueError):
    """A complete or incremental SPI transaction failed validation."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SpiCsiFrame:
    """One decoded SPI CSI frame with canonical ``[I, Q]`` samples."""

    stream_epoch: int
    frame_seq: int
    source_timestamp_us: int
    source_dropped_total: int
    queue_dropped_total: int
    spi_dropped_total: int
    transport_errors_total: int
    channel: int
    bandwidth_mhz: int
    rssi: int
    noise_floor: int
    agc_gain: int
    fft_gain: int
    rx_format: int
    flags: int
    profile_fingerprint: bytes
    csi_iq: npt.NDArray[np.int8]

    @property
    def usable_for_preprocessing(self) -> bool:
        """Whether this frame is valid data rather than diagnostics/calibration."""

        return bool(self.flags & FLAG_CSI_VALID) and not bool(self.flags & UNUSABLE_FLAGS)

    @property
    def cumulative_loss_total(self) -> int:
        """Conservative sum of the independently attributable loss counters."""

        return (
            self.source_dropped_total
            + self.queue_dropped_total
            + self.spi_dropped_total
            + self.transport_errors_total
        )


def profile_fingerprint(profile_id: str) -> bytes:
    """Return the stable 64-bit fingerprint carried in every SPI transaction."""

    normalized = profile_id.strip()
    if not normalized:
        raise ValueError("profile_id must not be blank")
    return hashlib.sha256(normalized.encode("utf-8")).digest()[:8]


def _validate_uint(name: str, value: int, maximum: int) -> None:
    if value < 0 or value > maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}")


def _validate_frame(frame: SpiCsiFrame) -> None:
    _validate_uint("stream_epoch", frame.stream_epoch, (1 << 64) - 1)
    if frame.stream_epoch == 0:
        raise ValueError("stream_epoch must be nonzero")
    _validate_uint("frame_seq", frame.frame_seq, MAX_SAFE_FRAME_SEQ)
    _validate_uint("source_timestamp_us", frame.source_timestamp_us, MAX_SOURCE_TIMESTAMP_US)
    for name in (
        "source_dropped_total",
        "queue_dropped_total",
        "spi_dropped_total",
        "transport_errors_total",
    ):
        _validate_uint(name, int(getattr(frame, name)), MAX_COUNTER)
    if frame.flags & ~KNOWN_FLAGS:
        raise ValueError("flags contain unknown bits")
    if not 1 <= frame.channel <= 255:
        raise ValueError("channel must be between 1 and 255")
    if frame.bandwidth_mhz not in (20, 40, 80, 160):
        raise ValueError("bandwidth_mhz is not a supported Wi-Fi bandwidth")
    if frame.rx_format != RX_FORMAT_HE_SU:
        raise ValueError(f"rx_format must be HE SU ({RX_FORMAT_HE_SU})")
    if len(frame.profile_fingerprint) != 8:
        raise ValueError("profile_fingerprint must contain exactly eight bytes")
    if frame.csi_iq.dtype != np.dtype(np.int8):
        raise ValueError("csi_iq must use signed int8")
    if frame.csi_iq.ndim != 2 or frame.csi_iq.shape[1] != 2:
        raise ValueError("csi_iq must have shape (subcarriers, 2)")
    if not 1 <= frame.csi_iq.shape[0] <= 1_024:
        raise ValueError("subcarrier count must be between 1 and 1024")
    for name, value, minimum, maximum in (
        ("rssi", frame.rssi, -128, 127),
        ("noise_floor", frame.noise_floor, -128, 127),
        ("agc_gain", frame.agc_gain, 0, 255),
        ("fft_gain", frame.fft_gain, -128, 127),
    ):
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")


def encode_spi_csi_frame(
    frame: SpiCsiFrame,
    *,
    wire_iq_order: Literal["IQ", "QI"] = "IQ",
) -> bytes:
    """Encode one DMA-aligned SPI transaction with CRC32."""

    _validate_frame(frame)
    if wire_iq_order == "IQ":
        payload_array = frame.csi_iq
        iq_order = IQ_ORDER_IQ
    elif wire_iq_order == "QI":
        payload_array = frame.csi_iq[:, (1, 0)]
        iq_order = IQ_ORDER_QI
    else:
        raise ValueError("wire_iq_order must be IQ or QI")
    payload = np.ascontiguousarray(payload_array, dtype=np.int8).tobytes()
    padding_size = (-(HEADER_STRUCT.size + len(payload) + CRC_STRUCT.size)) % 4
    frame_size = HEADER_STRUCT.size + len(payload) + padding_size + CRC_STRUCT.size
    if frame_size > MAX_SPI_FRAME_BYTES:
        raise ValueError("encoded SPI transaction exceeds MAX_SPI_FRAME_BYTES")

    header = HEADER_STRUCT.pack(
        SPI_MAGIC,
        SPI_VERSION,
        SPI_KIND_CSI,
        frame.flags,
        HEADER_STRUCT.size,
        0,
        frame_size,
        frame.stream_epoch,
        frame.frame_seq,
        frame.source_timestamp_us,
        frame.source_dropped_total,
        frame.queue_dropped_total,
        frame.spi_dropped_total,
        frame.transport_errors_total,
        frame.channel,
        frame.bandwidth_mhz,
        int(frame.csi_iq.shape[0]),
        len(payload),
        frame.rssi,
        frame.noise_floor,
        frame.agc_gain,
        frame.fft_gain,
        iq_order,
        frame.rx_format,
        0,
        frame.profile_fingerprint,
    )
    body = header + payload + bytes(padding_size)
    return body + CRC_STRUCT.pack(zlib.crc32(body) & 0xFFFF_FFFF)


def decode_spi_csi_frame(
    transaction: bytes,
    *,
    max_frame_bytes: int = MAX_SPI_FRAME_BYTES,
) -> SpiCsiFrame:
    """Validate and decode one complete SPI transaction."""

    minimum_size = HEADER_STRUCT.size + CRC_STRUCT.size
    if len(transaction) < minimum_size:
        raise SpiProtocolError("length", "SPI transaction is shorter than header and CRC")
    if len(transaction) > max_frame_bytes:
        raise SpiProtocolError("oversize", "SPI transaction exceeds the configured limit")
    if len(transaction) % 4:
        raise SpiProtocolError("alignment", "SPI transaction must be four-byte aligned")

    unpacked = HEADER_STRUCT.unpack(transaction[: HEADER_STRUCT.size])
    (
        magic,
        version,
        kind,
        flags,
        header_size,
        reserved,
        frame_size,
        stream_epoch,
        frame_seq,
        source_timestamp_us,
        source_dropped_total,
        queue_dropped_total,
        spi_dropped_total,
        transport_errors_total,
        channel,
        bandwidth_mhz,
        subcarrier_count,
        payload_len,
        rssi,
        noise_floor,
        agc_gain,
        fft_gain,
        iq_order,
        rx_format,
        reserved2,
        fingerprint,
    ) = unpacked

    def reject(reason: str, message: str) -> None:
        raise SpiProtocolError(reason, message)

    if magic != SPI_MAGIC:
        reject("magic", "unexpected SPI magic")
    if version != SPI_VERSION:
        reject("version", f"unsupported SPI version {version}")
    if kind != SPI_KIND_CSI:
        reject("kind", f"unsupported SPI record kind {kind}")
    if flags & ~KNOWN_FLAGS:
        reject("flags", "SPI transaction contains unknown flag bits")
    if header_size != HEADER_STRUCT.size or reserved != 0 or reserved2 != 0:
        reject("header", "SPI header size or reserved fields are invalid")
    if frame_size != len(transaction):
        reject("length", "SPI frame_size does not match the transaction length")
    if stream_epoch == 0:
        reject("stream_epoch", "stream_epoch must be nonzero")
    if frame_seq > MAX_SAFE_FRAME_SEQ:
        reject("frame_seq_range", "frame_seq exceeds the safe 53-bit range")
    if source_timestamp_us > MAX_SOURCE_TIMESTAMP_US:
        reject("timestamp_range", "source timestamp cannot be represented as signed int64")
    if not 1 <= channel <= 255:
        reject("channel", "channel is outside the supported range")
    if bandwidth_mhz not in (20, 40, 80, 160):
        reject("bandwidth", "unsupported Wi-Fi bandwidth")
    if rx_format != RX_FORMAT_HE_SU:
        reject("rx_format", "SPI CSI frame is not HE SU")
    if iq_order not in (IQ_ORDER_IQ, IQ_ORDER_QI):
        reject("iq_order", "unsupported IQ ordering")
    if not 1 <= subcarrier_count <= 1_024:
        reject("shape", "subcarrier count is outside the supported range")
    if payload_len != subcarrier_count * 2:
        reject("shape", "payload length does not match the CSI shape")

    payload_start = HEADER_STRUCT.size
    payload_end = payload_start + payload_len
    crc_start = len(transaction) - CRC_STRUCT.size
    if payload_end > crc_start:
        reject("length", "CSI payload overlaps the CRC")
    padding = transaction[payload_end:crc_start]
    if len(padding) > 3 or any(padding):
        reject("padding", "SPI alignment padding must contain at most three zero bytes")
    expected_crc = CRC_STRUCT.unpack(transaction[crc_start:])[0]
    actual_crc = zlib.crc32(transaction[:crc_start]) & 0xFFFF_FFFF
    if actual_crc != expected_crc:
        reject("crc", "SPI transaction CRC32 mismatch")

    wire_iq = np.frombuffer(transaction[payload_start:payload_end], dtype=np.int8).reshape(
        subcarrier_count,
        2,
    )
    if iq_order == IQ_ORDER_QI:
        canonical_iq = np.ascontiguousarray(wire_iq[:, (1, 0)], dtype=np.int8)
    else:
        canonical_iq = np.ascontiguousarray(wire_iq, dtype=np.int8)
    return SpiCsiFrame(
        stream_epoch=stream_epoch,
        frame_seq=frame_seq,
        source_timestamp_us=source_timestamp_us,
        source_dropped_total=source_dropped_total,
        queue_dropped_total=queue_dropped_total,
        spi_dropped_total=spi_dropped_total,
        transport_errors_total=transport_errors_total,
        channel=channel,
        bandwidth_mhz=bandwidth_mhz,
        rssi=rssi,
        noise_floor=noise_floor,
        agc_gain=agc_gain,
        fft_gain=fft_gain,
        rx_format=rx_format,
        flags=flags,
        profile_fingerprint=fingerprint,
        csi_iq=canonical_iq,
    )


def spi_frame_size_from_header(
    header: bytes,
    *,
    max_frame_bytes: int = MAX_SPI_FRAME_BYTES,
) -> int:
    """Validate a phase-one WGSP header and return its logical frame size.

    This is the bounded decision point used by a Pi SPI master before it clocks
    phase two.  Full flags, payload, padding, and CRC validation still occurs
    in :func:`decode_spi_csi_frame` after both phases are concatenated.
    """

    if len(header) != HEADER_STRUCT.size:
        raise SpiProtocolError("header_length", "SPI phase one must be exactly 80 bytes")
    unpacked = HEADER_STRUCT.unpack(header)
    magic, version, kind, _flags, header_size, reserved, raw_frame_size = unpacked[:7]
    frame_size = int(raw_frame_size)
    reserved2 = unpacked[-2]
    if magic != SPI_MAGIC:
        raise SpiProtocolError("magic", "unexpected SPI magic in phase-one header")
    if version != SPI_VERSION:
        raise SpiProtocolError("version", f"unsupported SPI version {version}")
    if kind != SPI_KIND_CSI:
        raise SpiProtocolError("kind", f"unsupported SPI record kind {kind}")
    if header_size != HEADER_STRUCT.size or reserved != 0 or reserved2 != 0:
        raise SpiProtocolError("header", "SPI header size or reserved fields are invalid")
    if (
        frame_size < HEADER_STRUCT.size + CRC_STRUCT.size
        or frame_size > max_frame_bytes
        or frame_size % 4
    ):
        raise SpiProtocolError("frame_size", "SPI logical frame size is invalid")
    return frame_size


class SpiCsiParser:
    """Bounded incremental parser for a Pi SPI-master byte stream."""

    def __init__(
        self,
        *,
        max_frame_bytes: int = MAX_SPI_FRAME_BYTES,
        on_reject: Callable[[str], None] | None = None,
    ) -> None:
        if max_frame_bytes < HEADER_STRUCT.size + CRC_STRUCT.size:
            raise ValueError("max_frame_bytes is too small")
        self.max_frame_bytes = max_frame_bytes
        self.on_reject = on_reject
        self._buffer = bytearray()

    def _reject(self, reason: str) -> None:
        if self.on_reject is not None:
            self.on_reject(reason)

    def feed(self, data: bytes) -> tuple[SpiCsiFrame, ...]:
        """Consume arbitrary chunks and return all complete valid frames."""

        if not data:
            return ()
        self._buffer.extend(data)
        frames: list[SpiCsiFrame] = []
        while True:
            magic_index = self._buffer.find(SPI_MAGIC)
            if magic_index < 0:
                if len(self._buffer) > len(SPI_MAGIC) - 1:
                    discarded = len(self._buffer) - (len(SPI_MAGIC) - 1)
                    del self._buffer[:discarded]
                    self._reject("resync")
                break
            if magic_index:
                del self._buffer[:magic_index]
                self._reject("resync")
            if len(self._buffer) < HEADER_STRUCT.size:
                break
            frame_size = struct.unpack_from("<I", self._buffer, FRAME_SIZE_OFFSET)[0]
            if (
                frame_size < HEADER_STRUCT.size + CRC_STRUCT.size
                or frame_size > self.max_frame_bytes
                or frame_size % 4
            ):
                del self._buffer[0]
                self._reject("frame_size")
                continue
            if len(self._buffer) < frame_size:
                break
            transaction = bytes(self._buffer[:frame_size])
            del self._buffer[:frame_size]
            try:
                frames.append(
                    decode_spi_csi_frame(
                        transaction,
                        max_frame_bytes=self.max_frame_bytes,
                    )
                )
            except SpiProtocolError as exc:
                self._reject(exc.reason)
        return tuple(frames)
