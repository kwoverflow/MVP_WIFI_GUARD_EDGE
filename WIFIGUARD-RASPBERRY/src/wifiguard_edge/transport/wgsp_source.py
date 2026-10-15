"""ESP32-C5 WGSP Batch8 SPI source for Raspberry Pi.

This adapter is deliberately limited to transport.  It converts the validated
320 Hz WGSP frames into the existing :class:`CsiFrame`/``RingBuffer`` contract,
so presence detection, feature extraction, calibration and MQTT publication do
not need a second implementation for SPI.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from collections import Counter
from typing import Any, ClassVar

import numpy as np

from ..csi.buffer import RingBuffer
from ..csi.protocol import CsiFrame
from .wgsp_protocol import AMFALL_SPI_FRAME_BYTES, SpiProtocolError, decode_spi_csi_frame

log = logging.getLogger("transport.wgsp")

MAX_SPI_TRANSACTION_BYTES = 8_192


class _SpiIocTransfer(ctypes.Structure):
    """Linux ``struct spi_ioc_transfer`` for one physical transaction."""

    _fields_: ClassVar[list[tuple[str, Any]]] = [
        ("tx_buf", ctypes.c_uint64),
        ("rx_buf", ctypes.c_uint64),
        ("len", ctypes.c_uint32),
        ("speed_hz", ctypes.c_uint32),
        ("delay_usecs", ctypes.c_uint16),
        ("bits_per_word", ctypes.c_uint8),
        ("cs_change", ctypes.c_uint8),
        ("tx_nbits", ctypes.c_uint8),
        ("rx_nbits", ctypes.c_uint8),
        ("word_delay_usecs", ctypes.c_uint8),
        ("pad", ctypes.c_uint8),
    ]


def _spi_iow(number: int, size: int) -> int:
    return (1 << 30) | (size << 16) | (ord("k") << 8) | number


def _spi_ior(number: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (ord("k") << 8) | number


_SPI_IOC_RD_MODE = _spi_ior(1, 1)
_SPI_IOC_RD_BITS_PER_WORD = _spi_ior(3, 1)
_SPI_IOC_MESSAGE_1 = _spi_iow(0, ctypes.sizeof(_SpiIocTransfer))


class LinuxDirectSpi:
    """Lazy, safe-open Linux spidev adapter used by the validated Pi path."""

    def __init__(self, *, bus: int, device: int, speed_hz: int, mode: int = 0) -> None:
        if not 1_000_000 <= speed_hz <= 50_000_000:
            raise ValueError("SPI speed must be between 1 MHz and 50 MHz")
        if mode not in range(4):
            raise ValueError("SPI mode must be in the range 0..3")
        self._path = f"/dev/spidev{bus}.{device}"
        self._speed_hz = speed_hz
        self._mode = mode
        self._fd: int | None = None
        self._fcntl: Any | None = None

    def _open(self) -> tuple[int, Any]:
        if self._fd is not None and self._fcntl is not None:
            return self._fd, self._fcntl
        try:
            import fcntl  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - Linux-only dependency
            raise RuntimeError("direct SPI ioctl is available only on Linux") from exc

        fd = os.open(self._path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        try:
            mode_buffer = bytearray(1)
            bits_buffer = bytearray(1)
            fcntl.ioctl(fd, _SPI_IOC_RD_MODE, mode_buffer, True)
            fcntl.ioctl(fd, _SPI_IOC_RD_BITS_PER_WORD, bits_buffer, True)
            current_mode = int.from_bytes(mode_buffer, byteorder=sys.byteorder)
            current_bits = int.from_bytes(bits_buffer, byteorder=sys.byteorder)
            if current_mode != self._mode or current_bits != 8:
                raise RuntimeError(
                    "SPI mode/bits must be configured before READY is asserted; "
                    f"expected mode={self._mode} bits=8, "
                    f"observed mode={current_mode} bits={current_bits}"
                )
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        self._fcntl = fcntl
        return fd, fcntl

    def transfer(self, byte_count: int) -> bytes:
        if not 0 < byte_count <= MAX_SPI_TRANSACTION_BYTES:
            raise ValueError("SPI transfer size is outside the WGSP bound")
        fd, fcntl = self._open()
        tx_buffer = (ctypes.c_uint8 * byte_count)()
        rx_buffer = (ctypes.c_uint8 * byte_count)()
        transfer = _SpiIocTransfer(
            tx_buf=ctypes.addressof(tx_buffer),
            rx_buf=ctypes.addressof(rx_buffer),
            len=byte_count,
            speed_hz=self._speed_hz,
            delay_usecs=0,
            bits_per_word=8,
            cs_change=0,
            tx_nbits=0,
            rx_nbits=0,
            word_delay_usecs=0,
            pad=0,
        )
        result = fcntl.ioctl(fd, _SPI_IOC_MESSAGE_1, bytearray(bytes(transfer)), True)
        if result != byte_count:
            raise OSError(f"SPI_IOC_MESSAGE returned {result} bytes; expected {byte_count}")
        return bytes(rx_buffer)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            self._fcntl = None


class WgspBatch8Source(threading.Thread):
    """READY-driven Batch8 source implementing the existing ``CsiSource`` contract."""

    def __init__(self, buffer: RingBuffer, config: Any) -> None:
        super().__init__(daemon=True, name="csi-wgsp-batch8")
        if config.spi_batch_frames not in (1, 2, 4, 8):
            raise ValueError("spi_batch_frames must be one of 1, 2, 4, 8")
        self.buffer = buffer
        self.config = config
        self._stop_event = threading.Event()
        self._connected = False
        self._frames_ok = 0
        self._protocol_rejects = 0
        self._protocol_resyncs = 0
        self._transport_errors = 0
        self._reject_reasons: Counter[str] = Counter()
        self._last_rssi: float | None = None
        self._last_epoch: int | None = None
        self._spi: LinuxDirectSpi | None = None
        self._ready_request: Any | None = None

    @property
    def running(self) -> bool:
        return self._connected and not self._stop_event.is_set() and self.is_alive()

    @property
    def packet_count(self) -> int:
        return self._frames_ok

    def stop(self) -> None:
        self._stop_event.set()

    def get_window(self, seconds: float):
        return self.buffer.get_window(seconds)

    def send_line(self, text: str) -> bool:
        # The current fixed WGSP contract is receive-only.  Calibration commands
        # need a future versioned control record instead of unframed SPI text.
        log.warning("WGSP command is unsupported: %s", text)
        return False

    def status(self) -> dict[str, Any]:
        ring = self.buffer.stats()
        return {
            "connected": self.running,
            "port": f"/dev/spidev{self.config.spi_bus}.{self.config.spi_device}",
            "baud": None,
            "reconnects": self._protocol_resyncs,
            "frames_ok": self._frames_ok,
            "checksum_errors": self._reject_reasons.get("crc", 0),
            "resyncs": self._protocol_resyncs,
            "mac_filtered": 0,
            "hz_1s": ring["hz_1s"],
            "rssi": self._last_rssi,
            "buffered_seconds": ring["buffered_seconds"],
        }

    def _open_ready(self) -> Any:
        try:
            import gpiod  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - Pi-only dependency
            raise RuntimeError("install the project 'pi' extra on Raspberry Pi") from exc
        bias_values = {
            "as-is": gpiod.line.Bias.AS_IS,
            "disabled": gpiod.line.Bias.DISABLED,
            "pull-down": gpiod.line.Bias.PULL_DOWN,
            "pull-up": gpiod.line.Bias.PULL_UP,
        }
        settings = gpiod.LineSettings(
            direction=gpiod.line.Direction.INPUT,
            bias=bias_values[self.config.ready_bias],
            edge_detection=gpiod.line.Edge.BOTH,
        )
        return gpiod.request_lines(
            self.config.ready_gpio_chip,
            consumer="wifiguard-wgsp-ready",
            config={self.config.gpio_data_ready: settings},
        )

    def _is_ready(self) -> bool:
        import gpiod  # type: ignore[import-not-found]

        assert self._ready_request is not None
        return bool(
            self._ready_request.get_value(self.config.gpio_data_ready)
            == gpiod.line.Value.ACTIVE
        )

    def _drain_edges(self) -> None:
        assert self._ready_request is not None
        while self._ready_request.wait_edge_events(0):
            self._ready_request.read_edge_events()

    def _wait_ready(self) -> bool:
        assert self._ready_request is not None
        while not self._stop_event.is_set():
            if self._is_ready():
                self._drain_edges()
                return True
            if self._ready_request.wait_edge_events(0.05):
                self._drain_edges()
        return False

    def _wait_transaction_edge(self) -> bool:
        """Wait for the receiver's READY fall/rise handshake after the clocks."""

        assert self._ready_request is not None
        deadline = time.monotonic() + 0.1
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            if self._ready_request.wait_edge_events(0.01):
                self._drain_edges()
                return True
        return not self._stop_event.is_set()

    def _append(self, frame: Any) -> None:
        if not frame.usable_for_preprocessing:
            self._reject_reasons["unusable_flags"] += 1
            return
        iq = frame.csi_iq.astype(np.float32)
        amps = np.sqrt(iq[:, 0] * iq[:, 0] + iq[:, 1] * iq[:, 1]).astype(np.float32)
        if self._last_epoch is not None and self._last_epoch != frame.stream_epoch:
            log.info("WGSP stream epoch changed: %s -> %s", self._last_epoch, frame.stream_epoch)
        self._last_epoch = frame.stream_epoch
        self.buffer.append(
            CsiFrame(
                seq=frame.frame_seq,
                mac="00:00:00:00:00:00",
                rssi=frame.rssi,
                noise_floor=frame.noise_floor,
                channel=frame.channel,
                timestamp_us=frame.source_timestamp_us & 0xFFFF_FFFF,
                fft_gain=frame.fft_gain,
                agc_gain=frame.agc_gain,
                csi_len=int(frame.csi_iq.size),
                amps=amps,
                host_time=time.monotonic(),
            )
        )
        self._frames_ok += 1
        self._last_rssi = float(frame.rssi)

    def run(self) -> None:
        transfer_bytes = AMFALL_SPI_FRAME_BYTES * self.config.spi_batch_frames
        try:
            self._ready_request = self._open_ready()
            self._spi = LinuxDirectSpi(
                bus=self.config.spi_bus,
                device=self.config.spi_device,
                speed_hz=self.config.max_speed_hz,
            )
            self._connected = True
            while not self._stop_event.is_set():
                if not self._wait_ready():
                    break
                try:
                    batch = self._spi.transfer(transfer_bytes)
                except (OSError, RuntimeError, ValueError, OverflowError):
                    self._transport_errors += 1
                    log.exception("WGSP SPI transfer failed")
                    self._spi.close()
                    time.sleep(self.config.spi_protocol_resync_seconds)
                    continue

                self._wait_transaction_edge()
                valid = 0
                for offset in range(0, len(batch), AMFALL_SPI_FRAME_BYTES):
                    transaction = batch[offset : offset + AMFALL_SPI_FRAME_BYTES]
                    try:
                        frame = decode_spi_csi_frame(transaction)
                    except SpiProtocolError as exc:
                        self._protocol_rejects += 1
                        self._reject_reasons[exc.reason] += 1
                        continue
                    valid += 1
                    self._append(frame)

                if valid == 0:
                    # Field recovery: the receiver retains the batch, while
                    # reopening the Pi descriptor clears an all-zero/stale read.
                    self._spi.close()
                    self._protocol_resyncs += 1
                    time.sleep(self.config.spi_protocol_resync_seconds)
        finally:
            self._connected = False
            if self._spi is not None:
                self._spi.close()
            if self._ready_request is not None:
                self._ready_request.release()
