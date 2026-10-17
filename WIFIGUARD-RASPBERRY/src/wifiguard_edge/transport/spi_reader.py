"""
SPI Master Interface for Raspberry Pi
Communicates with ESP32C5 RX via SPI and GPIO interrupt
"""

import spidev
import RPi.GPIO as GPIO
import threading
import time
import logging
from typing import Callable, Optional
from collections import deque

from . import protocol

logger = logging.getLogger(__name__)

# GPIO Pin Configuration (BCM numbering)
GPIO_DATA_READY = 25   # Input: ESP32C5 signals data ready
GPIO_CE0 = 8           # CS pin (handled by spidev)

class SPIInterface:
    """SPI Master Interface for ESP32C5 RX communication."""

    def __init__(self, on_frame_cb: Optional[Callable] = None, on_error_cb: Optional[Callable] = None):
        """
        Initialize SPI interface.

        Args:
            on_frame_cb: Callback when frame received: (frame_data: bytes, payload_len: int)
            on_error_cb: Callback on error: (error_msg: str)
        """
        self.spi = None
        self.on_frame_cb = on_frame_cb
        self.on_error_cb = on_error_cb

        self._pending_cmd = None
        self._pending_param = 0
        self._running = False
        self._rx_thread = None
        self._retry_count = 0
        self._max_retries = 3
        self._data_ready_event = threading.Event()
        self._last_contact = 0.0  # Timestamp of last valid frame received

        self._init_gpio()
        self._init_spi()

    def _init_gpio(self):
        """Initialize GPIO for data ready interrupt."""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(GPIO_DATA_READY, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(GPIO_DATA_READY, GPIO.RISING, callback=self._on_data_ready)
            logger.info(f"GPIO {GPIO_DATA_READY} configured for data ready interrupt")
        except Exception as e:
            logger.error(f"GPIO initialization failed: {e}")
            if self.on_error_cb:
                self.on_error_cb(f"GPIO init failed: {e}")

    def _init_spi(self):
        """Initialize SPI bus."""
        try:
            self.spi = spidev.SpiDev()
            self.spi.open(0, 0)  # Bus 0, Device 0 (CE0)
            self.spi.max_speed_hz = 10_000_000  # 10 MHz
            self.spi.mode = 0
            self.spi.lsb_first = False
            logger.info("SPI bus initialized: 10 MHz, mode 0")
        except Exception as e:
            logger.error(f"SPI initialization failed: {e}")
            if self.on_error_cb:
                self.on_error_cb(f"SPI init failed: {e}")

    def _on_data_ready(self, channel):
        """GPIO interrupt callback when ESP32C5 signals data ready."""
        if self._running:
            self._data_ready_event.set()

    def _spi_transfer(self, frame_size: int = protocol.SPI_FRAME_SIZE_SMALL) -> Optional[bytes]:
        """
        Perform SPI transaction.

        Args:
            frame_size: Size of frame to receive (256 or 4096 bytes)

        Returns:
            Received frame bytes or None on error
        """
        if not self.spi:
            return None

        try:
            # Build TX buffer with pending command or zeros
            tx_buf = bytearray(frame_size)

            if self._pending_cmd is not None:
                # Build command frame header
                hdr = protocol.SpiFrameHdr(
                    magic=protocol.SPI_MAGIC_RPi_TO_RX,
                    version=protocol.SPI_VERSION,
                    mode=protocol.SpiMode.OCCUPANCY,
                    pkt_type=protocol.SpiPktType.CMD,
                    num_frames=0,
                    payload_len=4
                )
                cmd_payload = protocol.RpiCmdPayload(
                    cmd=self._pending_cmd,
                    param=self._pending_param
                )

                hdr_bytes = hdr.pack()
                cmd_bytes = cmd_payload.pack()

                tx_buf[:8] = hdr_bytes
                tx_buf[8:12] = cmd_bytes

                logger.debug(f"Sending command: cmd=0x{self._pending_cmd:02x}, param={self._pending_param}")
                self._pending_cmd = None
                self._pending_param = 0

            # SPI transfer
            rx_buf = self.spi.xfer2(list(tx_buf))
            return bytes(rx_buf)

        except Exception as e:
            logger.warning(f"SPI transfer error: {e}")
            return None

    def _rx_worker(self):
        """Background thread for RX processing."""
        logger.info("RX worker thread started")

        while self._running:
            try:
                # Wait for data ready signal from GPIO interrupt (with 1 second timeout fallback)
                if self._data_ready_event.wait(timeout=1.0):
                    self._data_ready_event.clear()
                    logger.debug("Data ready signal received")
                else:
                    # Timeout - periodic fallback in case interrupt missed
                    if not self._running:
                        break
                    # If pending command, flush it during timeout
                    if self._pending_cmd is not None:
                        self._spi_transfer(protocol.SPI_FRAME_SIZE_LARGE)
                    continue

                # Attempt to receive frame (always LARGE to accommodate CSI_BATCH)
                frame_data = self._spi_transfer(protocol.SPI_FRAME_SIZE_LARGE)

                if frame_data:
                    try:
                        frame, payload_len = protocol.SpiFrame.unpack(frame_data)

                        # Validate magic
                        if (frame.hdr.magic[0] == protocol.SPI_MAGIC_RX_TO_RPi[0] and
                            frame.hdr.magic[1] == protocol.SPI_MAGIC_RX_TO_RPi[1]):

                            self._last_contact = time.time()  # Track connection

                            if self.on_frame_cb:
                                self.on_frame_cb(frame, payload_len)

                            self._retry_count = 0

                        else:
                            logger.warning("Invalid magic bytes in frame")

                    except Exception as e:
                        logger.warning(f"Frame unpack error: {e}")

                else:
                    self._retry_count += 1
                    if self._retry_count >= self._max_retries:
                        logger.error(f"SPI communication lost after {self._max_retries} retries")
                        if self.on_error_cb:
                            self.on_error_cb("SPI communication lost")
                        self._retry_count = 0
                        time.sleep(1)  # Wait before retry

            except Exception as e:
                logger.error(f"RX worker error: {e}")
                if self.on_error_cb:
                    self.on_error_cb(f"RX worker error: {e}")
                time.sleep(1)

        logger.info("RX worker thread stopped")

    def start(self):
        """Start receiving frames."""
        if self._running:
            logger.warning("RX already running")
            return

        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_worker, daemon=False)
        self._rx_thread.start()
        logger.info("SPI RX started")

    def stop(self):
        """Stop receiving frames."""
        self._running = False
        if self._rx_thread:
            self._rx_thread.join(timeout=5)
        logger.info("SPI RX stopped")

    def send_cmd(self, cmd: int, param: int = 0):
        """
        Queue command to send to ESP32C5.

        Args:
            cmd: Command code (CMD_*)
            param: Parameter (interpretation depends on cmd)
        """
        self._pending_cmd = cmd
        self._pending_param = param
        logger.info(f"Queued command: 0x{cmd:02x}, param={param}")

    def send_start_train(self, duration_s: int = protocol.TRAIN_TOTAL_S):
        """Start training on ESP32C5."""
        self.send_cmd(protocol.RpiCmd.START_TRAIN, duration_s)

    def send_mode_occupancy(self):
        """Switch ESP32C5 to occupancy detection mode."""
        self.send_cmd(protocol.RpiCmd.MODE_OCCUPANCY)

    def send_mode_fall(self):
        """Switch ESP32C5 to fall detection mode."""
        self.send_cmd(protocol.RpiCmd.MODE_FALL)

    def send_ping(self):
        """Send ping to ESP32C5."""
        self.send_cmd(protocol.RpiCmd.PING)

    def close(self):
        """Close SPI and GPIO resources."""
        self.stop()
        if self.spi:
            self.spi.close()
        try:
            GPIO.cleanup()
        except:
            pass
        logger.info("SPI interface closed")

    def is_connected(self) -> bool:
        """Check if ESP32 is currently communicating."""
        return time.time() - self._last_contact < 3.0


if __name__ == "__main__":
    # Simple test
    logging.basicConfig(level=logging.DEBUG)

    def on_frame(frame, payload_len):
        logger.info(f"Received frame: mode={frame.hdr.mode}, pkt_type={frame.hdr.pkt_type}, "
                   f"payload_len={payload_len}")
        if payload_len > 0:
            payload = frame.payload[:payload_len]
            logger.info(f"Payload: {payload}")

    def on_error(msg):
        logger.error(f"Error: {msg}")

    try:
        spi = SPIInterface(on_frame_cb=on_frame, on_error_cb=on_error)
        spi.start()

        time.sleep(2)
        spi.send_ping()

        time.sleep(5)
        spi.stop()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        spi.close()
