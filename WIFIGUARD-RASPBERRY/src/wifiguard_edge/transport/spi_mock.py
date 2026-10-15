"""
SPI Mock Interface for Windows Development
State-aware mock that generates appropriate packets based on pipeline state
"""

import threading
import time
import logging
from typing import Callable, Optional
from enum import IntEnum
import random
import json

from . import protocol

logger = logging.getLogger(__name__)


class MockState(IntEnum):
    IDLE = 0
    TRAINING = 1
    OCCUPANCY = 2
    FALL_DETECTING = 3


# Predefined occupancy scenario: (duration_s, entry, motion, jitter_smooth, wander, occ_confirmed, req_mode_change)
_OCC_SCENARIO = [
    (5,  False, False, 0.021, 0.001, False, ""),      # empty room
    (3,  True,  True,  0.045, 0.008, False, ""),      # person entering
    (8,  True,  True,  0.038, 0.006, True,  ""),      # person settled
    (5,  True,  True,  0.042, 0.007, True,  "fall"),  # motion triggers fall mode request
    (10, True,  False, 0.022, 0.003, True,  ""),      # person still
    (4,  True,  True,  0.049, 0.009, True,  ""),      # person moving again
    (6,  False, False, 0.020, 0.001, False, ""),      # person left
]


class SPIInterface:
    """Mock SPI Interface for Windows development with state-aware packet generation."""

    def __init__(self, on_frame_cb: Optional[Callable] = None, on_error_cb: Optional[Callable] = None):
        """
        Initialize mock SPI interface.

        Args:
            on_frame_cb: Callback when frame received: (frame, payload_len)
            on_error_cb: Callback on error: (error_msg: str)
        """
        self.on_frame_cb = on_frame_cb
        self.on_error_cb = on_error_cb

        self._state_lock = threading.Lock()
        self._mock_state = MockState.IDLE

        # Training simulation fields
        self._train_start_time: Optional[float] = None
        self._train_duration_s: int = protocol.TRAIN_TOTAL_S

        # Occupancy simulation fields
        self._occ_scenario_step: int = 0
        self._occ_step_start_t: float = 0.0

        # Threading
        self._running = False
        self._rx_thread = None
        self._retry_count = 0
        self._max_retries = 3

        # CSI generation
        self._frame_counter = 0

        logger.info("Mock SPI interface initialized (Windows mode)")

    def _generate_idle_frame(self) -> bytes:
        """Generate empty frame during IDLE state (keepalive)."""
        try:
            hdr = protocol.SpiFrameHdr(
                magic=protocol.SPI_MAGIC_RX_TO_RPi,
                version=protocol.SPI_VERSION,
                mode=protocol.SpiMode.OCCUPANCY,
                pkt_type=protocol.SpiPktType.STATUS_JSON,
                num_frames=0,
                payload_len=0
            )

            hdr_bytes = hdr.pack()
            full_frame = hdr_bytes + bytes(protocol.SPI_FRAME_SIZE_LARGE - len(hdr_bytes))
            return full_frame[:protocol.SPI_FRAME_SIZE_LARGE]

        except Exception as e:
            logger.error(f"Idle frame generation error: {e}")
            return None

    def _generate_train_progress_frame(self, elapsed_s: int, total_s: int, sample_count: int) -> bytes:
        """Generate TRAIN_PROGRESS packet."""
        try:
            payload_dict = {
                "elapsed_s": elapsed_s,
                "total_s": total_s,
                "sample_count": sample_count,
                "current_rssi_mean": -61.0
            }
            payload_bytes = json.dumps(payload_dict, separators=(',', ':')).encode('utf-8')

            hdr = protocol.SpiFrameHdr(
                magic=protocol.SPI_MAGIC_RX_TO_RPi,
                version=protocol.SPI_VERSION,
                mode=protocol.SpiMode.TRAIN,
                pkt_type=protocol.SpiPktType.TRAIN_PROGRESS,
                num_frames=0,
                payload_len=len(payload_bytes)
            )

            hdr_bytes = hdr.pack()
            full_frame = hdr_bytes + payload_bytes + bytes(
                protocol.SPI_FRAME_SIZE_LARGE - len(hdr_bytes) - len(payload_bytes)
            )
            return full_frame[:protocol.SPI_FRAME_SIZE_LARGE]

        except Exception as e:
            logger.error(f"TRAIN_PROGRESS frame generation error: {e}")
            return None

    def _generate_train_done_frame(self) -> bytes:
        """Generate TRAIN_DONE packet."""
        try:
            payload_dict = {
                "base_rssi_mean": -61.0,
                "base_rssi_var": 2.5,
                "base_amp_mean": 0.48,
                "base_amp_std": 0.15,
                "sample_count": 1000
            }
            payload_bytes = json.dumps(payload_dict, separators=(',', ':')).encode('utf-8')

            hdr = protocol.SpiFrameHdr(
                magic=protocol.SPI_MAGIC_RX_TO_RPi,
                version=protocol.SPI_VERSION,
                mode=protocol.SpiMode.TRAIN,
                pkt_type=protocol.SpiPktType.TRAIN_DONE,
                num_frames=0,
                payload_len=len(payload_bytes)
            )

            hdr_bytes = hdr.pack()
            full_frame = hdr_bytes + payload_bytes + bytes(
                protocol.SPI_FRAME_SIZE_LARGE - len(hdr_bytes) - len(payload_bytes)
            )
            return full_frame[:protocol.SPI_FRAME_SIZE_LARGE]

        except Exception as e:
            logger.error(f"TRAIN_DONE frame generation error: {e}")
            return None

    def _generate_status_json_frame(self, entry: bool, motion: bool, jitter_smooth: float,
                                   wander: float, occ_confirmed: bool, req_mode_change: str) -> bytes:
        """Generate STATUS_JSON packet."""
        try:
            payload_dict = {
                "entry": entry,
                "motion": motion,
                "jitter_smooth": round(jitter_smooth, 4),
                "wander": round(wander, 4),
                "occupancy_confirmed": occ_confirmed,
                "req_mode_change": req_mode_change,
                "rssi": -60 + random.uniform(-3, 3),
                "rssi_variance": 2.5,
                "ts_ms": int(time.time() * 1000)
            }
            payload_bytes = json.dumps(payload_dict, separators=(',', ':')).encode('utf-8')

            hdr = protocol.SpiFrameHdr(
                magic=protocol.SPI_MAGIC_RX_TO_RPi,
                version=protocol.SPI_VERSION,
                mode=protocol.SpiMode.OCCUPANCY,
                pkt_type=protocol.SpiPktType.STATUS_JSON,
                num_frames=0,
                payload_len=len(payload_bytes)
            )

            hdr_bytes = hdr.pack()
            full_frame = hdr_bytes + payload_bytes + bytes(
                protocol.SPI_FRAME_SIZE_LARGE - len(hdr_bytes) - len(payload_bytes)
            )
            return full_frame[:protocol.SPI_FRAME_SIZE_LARGE]

        except Exception as e:
            logger.error(f"STATUS_JSON frame generation error: {e}")
            return None

    def _generate_csi_batch_frame(self) -> bytes:
        """Generate CSI_BATCH packet (16 CSI frames)."""
        try:
            frames = []
            for _ in range(16):
                csi_len = min(len(self._generate_mock_csi_data(128)), protocol.CSI_MAX_LEN)
                csi_data = self._generate_mock_csi_data(csi_len)

                frame = protocol.CsiRawFrame(
                    seq=self._frame_counter,
                    timestamp_ms=int(time.time() * 1000),
                    rssi=-50 + random.randint(-10, 10),
                    noise_floor=100,
                    csi_len=csi_len,
                    csi_data=csi_data
                )
                self._frame_counter += 1
                frames.append(frame.pack())

            payload_bytes = b''.join(frames)

            hdr = protocol.SpiFrameHdr(
                magic=protocol.SPI_MAGIC_RX_TO_RPi,
                version=protocol.SPI_VERSION,
                mode=protocol.SpiMode.FALL_DETECT,
                pkt_type=protocol.SpiPktType.CSI_BATCH,
                num_frames=16,
                payload_len=len(payload_bytes)
            )

            hdr_bytes = hdr.pack()
            full_frame = hdr_bytes + payload_bytes + bytes(
                protocol.SPI_FRAME_SIZE_LARGE - len(hdr_bytes) - len(payload_bytes)
            )
            return full_frame[:protocol.SPI_FRAME_SIZE_LARGE]

        except Exception as e:
            logger.error(f"CSI_BATCH frame generation error: {e}")
            return None

    def _generate_mock_csi_data(self, size: int = 256) -> bytes:
        """Generate synthetic CSI data for testing."""
        csi_data = bytearray()
        for _ in range(size // 2):
            I = random.randint(-128, 127)
            Q = random.randint(-128, 127)
            csi_data.append(I & 0xFF)
            csi_data.append(Q & 0xFF)
        return bytes(csi_data)

    def _run_training_tick(self) -> Optional[bytes]:
        """Generate frame during TRAINING state."""
        now = time.time()
        elapsed_s = int(now - self._train_start_time)
        total_s = self._train_duration_s

        if elapsed_s >= total_s:
            # Training complete: switch to OCCUPANCY
            frame = self._generate_train_done_frame()
            with self._state_lock:
                self._mock_state = MockState.OCCUPANCY
                self._occ_step_start_t = time.time()
                self._occ_scenario_step = 0
            logger.info("[MOCK] Training complete, auto-switching to OCCUPANCY")
            return frame

        sample_count = max(0, (elapsed_s - protocol.TRAIN_WARMUP_S) * 50)
        return self._generate_train_progress_frame(elapsed_s, total_s, sample_count)

    def _run_occupancy_tick(self) -> Optional[bytes]:
        """Generate frame during OCCUPANCY state."""
        now = time.time()
        step = self._occ_scenario_step % len(_OCC_SCENARIO)
        dur, entry, motion, jitter_base, wander_base, occ_confirmed, req_mode = _OCC_SCENARIO[step]

        # Check if step duration expired
        if now - self._occ_step_start_t >= dur:
            self._occ_scenario_step += 1
            self._occ_step_start_t = now
            step = self._occ_scenario_step % len(_OCC_SCENARIO)
            dur, entry, motion, jitter_base, wander_base, occ_confirmed, req_mode = _OCC_SCENARIO[step]

        # Add Gaussian noise to values
        jitter = max(0.015, jitter_base + random.gauss(0, jitter_base * 0.08))
        wander = max(0.0005, wander_base + random.gauss(0, wander_base * 0.08))

        return self._generate_status_json_frame(entry, motion, jitter, wander, occ_confirmed, req_mode)

    def _rx_worker(self):
        """Background thread for mock RX processing."""
        logger.info("Mock RX worker thread started")

        while self._running:
            try:
                with self._state_lock:
                    current_state = self._mock_state

                if current_state == MockState.IDLE:
                    frame_data = self._generate_idle_frame()
                    sleep_s = 1.0

                elif current_state == MockState.TRAINING:
                    frame_data = self._run_training_tick()
                    sleep_s = 0.25

                elif current_state == MockState.OCCUPANCY:
                    frame_data = self._run_occupancy_tick()
                    sleep_s = 0.1

                elif current_state == MockState.FALL_DETECTING:
                    frame_data = self._generate_csi_batch_frame()
                    sleep_s = 0.05

                else:
                    time.sleep(0.1)
                    continue

                if frame_data:
                    try:
                        frame, payload_len = protocol.SpiFrame.unpack(frame_data)

                        if (frame.hdr.magic[0] == protocol.SPI_MAGIC_RX_TO_RPi[0] and
                            frame.hdr.magic[1] == protocol.SPI_MAGIC_RX_TO_RPi[1]):

                            if self.on_frame_cb:
                                self.on_frame_cb(frame, payload_len)

                            self._retry_count = 0

                        else:
                            logger.warning("Invalid magic bytes in mock frame")

                    except Exception as e:
                        logger.warning(f"Mock frame unpack error: {e}")

                else:
                    self._retry_count += 1
                    if self._retry_count >= self._max_retries:
                        logger.error(f"Mock frame generation failed after {self._max_retries} retries")
                        if self.on_error_cb:
                            self.on_error_cb("Mock frame generation error")
                        self._retry_count = 0
                        time.sleep(1)

                time.sleep(sleep_s)

            except Exception as e:
                logger.error(f"Mock RX worker error: {e}")
                if self.on_error_cb:
                    self.on_error_cb(f"Mock RX worker error: {e}")
                time.sleep(1)

        logger.info("Mock RX worker thread stopped")

    def start(self):
        """Start receiving mock frames."""
        if self._running:
            logger.warning("Mock RX already running")
            return

        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_worker, daemon=False)
        self._rx_thread.start()
        logger.info("Mock SPI RX started")

    def stop(self):
        """Stop receiving mock frames."""
        self._running = False
        if self._rx_thread:
            self._rx_thread.join(timeout=5)
        logger.info("Mock SPI RX stopped")

    def send_start_train(self, duration_s: int = protocol.TRAIN_TOTAL_S):
        """Mock: Start training on ESP32C5."""
        with self._state_lock:
            self._mock_state = MockState.TRAINING
            self._train_start_time = time.time()
            self._train_duration_s = duration_s
        logger.info(f"[MOCK] Training started for {duration_s}s")

    def send_mode_occupancy(self):
        """Mock: Switch ESP32C5 to occupancy detection mode."""
        with self._state_lock:
            self._mock_state = MockState.OCCUPANCY
            self._occ_step_start_t = time.time()
            self._occ_scenario_step = 0
        logger.info("[MOCK] Switched to OCCUPANCY state")

    def send_mode_fall(self):
        """Mock: Switch ESP32C5 to fall detection mode."""
        with self._state_lock:
            self._mock_state = MockState.FALL_DETECTING
        logger.info("[MOCK] Switched to FALL_DETECTING state")

    def send_ping(self):
        """Mock: Send ping to ESP32C5."""
        logger.info("[MOCK] Ping sent")

    def close(self):
        """Close mock SPI resources."""
        self.stop()
        logger.info("Mock SPI interface closed")

    def is_connected(self) -> bool:
        """Check if mock SPI is actively running."""
        return self._running and self._rx_thread is not None and self._rx_thread.is_alive()


if __name__ == "__main__":
    # Simple mock test
    logging.basicConfig(level=logging.DEBUG)

    def on_frame(frame, payload_len):
        logger.info(f"[MOCK] Received frame: mode={frame.hdr.mode}, pkt_type={frame.hdr.pkt_type}, "
                   f"payload_len={payload_len}")

    def on_error(msg):
        logger.error(f"[MOCK] Error: {msg}")

    try:
        spi = SPIInterface(on_frame_cb=on_frame, on_error_cb=on_error)
        spi.start()

        logger.info("Starting training simulation...")
        time.sleep(1)
        spi.send_start_train(30)

        time.sleep(35)
        logger.info("Switching to occupancy mode...")
        spi.send_mode_occupancy()

        time.sleep(10)
        logger.info("Switching to fall detection mode...")
        spi.send_mode_fall()

        time.sleep(5)
        spi.stop()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        spi.close()
