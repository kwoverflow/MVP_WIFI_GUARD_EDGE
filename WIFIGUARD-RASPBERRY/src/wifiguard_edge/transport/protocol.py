"""
Common Protocol Definitions for Fall Detection System
Shared between ESP32C5 (TX/RX) and Raspberry Pi
"""

import struct
import json
from typing import Tuple, Optional, Dict, Any
from dataclasses import dataclass
from enum import IntEnum

# ============================================================================
# ESP-NOW Protocol (RX <-> TX)
# ============================================================================

class EspnowPktType(IntEnum):
    COUNTER = 0x00  # TX->RX: trigger packet
    CMD = 0x01      # RX->TX: command
    ACK = 0xFF      # TX->RX: acknowledgement

class EspnowCmd(IntEnum):
    SET_RATE = 0x01  # Change sampling rate

@dataclass
class EspnowCmdPacket:
    pkt_type: int = EspnowPktType.CMD
    cmd: int = EspnowCmd.SET_RATE
    pps: int = 50  # 50 or 320

    def pack(self) -> bytes:
        return struct.pack('<BBH', self.pkt_type, self.cmd, self.pps)

    @classmethod
    def unpack(cls, data: bytes) -> 'EspnowCmdPacket':
        if len(data) < 4:
            raise ValueError(f"Invalid packet size: {len(data)}")
        pkt_type, cmd, pps = struct.unpack('<BBH', data[:4])
        return cls(pkt_type=pkt_type, cmd=cmd, pps=pps)

# ============================================================================
# SPI Protocol (RX <-> RPi)
# ============================================================================

class SpiMode(IntEnum):
    TRAIN = 0x00
    OCCUPANCY = 0x01
    FALL_DETECT = 0x02

class SpiPktType(IntEnum):
    TRAIN_PROGRESS = 0x00
    TRAIN_DONE = 0x01
    STATUS_JSON = 0x02
    CSI_BATCH = 0x03
    CMD = 0x10
    ACK = 0xFF

class RpiCmd(IntEnum):
    START_TRAIN = 0x00
    MODE_OCCUPANCY = 0x10
    MODE_FALL = 0x11
    PING = 0x20

# Frame sizes
SPI_FRAME_SIZE_SMALL = 256
SPI_FRAME_SIZE_LARGE = 4096

# Magic bytes
SPI_MAGIC_RX_TO_RPi = bytes([0xAB, 0xCD])
SPI_MAGIC_RPi_TO_RX = bytes([0xCD, 0xAB])
SPI_VERSION = 0x01

CSI_MAX_LEN = 128

@dataclass
class SpiFrameHdr:
    magic: bytes = SPI_MAGIC_RX_TO_RPi
    version: int = SPI_VERSION
    mode: int = SpiMode.TRAIN
    pkt_type: int = SpiPktType.TRAIN_PROGRESS
    num_frames: int = 0
    payload_len: int = 0

    def pack(self) -> bytes:
        if len(self.magic) != 2:
            raise ValueError(f"Invalid magic length: {len(self.magic)}")
        return struct.pack(
            '<2sBBBBH',
            self.magic,
            self.version,
            self.mode,
            self.pkt_type,
            self.num_frames,
            self.payload_len
        )

    @classmethod
    def unpack(cls, data: bytes) -> 'SpiFrameHdr':
        if len(data) < 8:
            raise ValueError(f"Invalid header size: {len(data)}")
        magic, version, mode, pkt_type, num_frames, payload_len = \
            struct.unpack('<2sBBBBH', data[:8])
        return cls(
            magic=magic,
            version=version,
            mode=mode,
            pkt_type=pkt_type,
            num_frames=num_frames,
            payload_len=payload_len
        )

@dataclass
class RpiCmdPayload:
    cmd: int = RpiCmd.START_TRAIN
    param: int = 0  # for CMD_START_TRAIN: training duration in seconds

    def pack(self) -> bytes:
        return struct.pack('<BHB', self.cmd, self.param, 0)  # reserved

    @classmethod
    def unpack(cls, data: bytes) -> 'RpiCmdPayload':
        if len(data) < 4:
            raise ValueError(f"Invalid payload size: {len(data)}")
        cmd, param, _ = struct.unpack('<BHB', data[:4])
        return cls(cmd=cmd, param=param)

@dataclass
class CsiRawFrame:
    seq: int = 0
    timestamp_ms: int = 0
    rssi: int = 0
    noise_floor: int = 0
    csi_len: int = 0
    drop_count: int = 0
    csi_data: bytes = b''  # max 128 bytes

    def pack(self) -> bytes:
        if len(self.csi_data) > CSI_MAX_LEN:
            raise ValueError(f"CSI data too long: {len(self.csi_data)}")
        # Pad to CSI_MAX_LEN
        csi_padded = self.csi_data + b'\x00' * (CSI_MAX_LEN - len(self.csi_data))
        return struct.pack(
            '<IIBBB',
            self.seq,
            self.timestamp_ms,
            self.rssi if self.rssi >= 0 else (256 + self.rssi),  # handle negative
            self.noise_floor,
            self.csi_len,
        ) + struct.pack('B', self.drop_count) + csi_padded

    @classmethod
    def unpack(cls, data: bytes) -> 'CsiRawFrame':
        if len(data) < 140:
            raise ValueError(f"Invalid CSI frame size: {len(data)}")
        seq, ts, rssi, nf, csi_len = struct.unpack(
            '<IIBBB',
            data[:11]
        )
        drop = struct.unpack('B', data[11:12])[0]
        # Handle negative rssi
        if rssi > 127:
            rssi = rssi - 256
        csi_data = data[12:12 + csi_len]
        return cls(
            seq=seq,
            timestamp_ms=ts,
            rssi=rssi,
            noise_floor=nf,
            csi_len=csi_len,
            drop_count=drop,
            csi_data=csi_data
        )

    @property
    def size(self) -> int:
        return 140  # fixed size

# ============================================================================
# SPI Frame Wrapper (Header + Payload)
# ============================================================================

class SpiFrame:
    def __init__(self, hdr: SpiFrameHdr, payload: bytes):
        self.hdr = hdr
        self.payload = payload

    def pack(self, frame_size: int = SPI_FRAME_SIZE_SMALL) -> bytes:
        """Pack frame into fixed-size buffer."""
        if len(self.payload) > frame_size - 8:
            raise ValueError(f"Payload too large for frame size {frame_size}")

        self.hdr.payload_len = len(self.payload)
        hdr_bytes = self.hdr.pack()

        # Pad payload to frame_size
        padded = hdr_bytes + self.payload + b'\x00' * (frame_size - 8 - len(self.payload))
        return padded[:frame_size]

    @classmethod
    def unpack(cls, data: bytes) -> Tuple['SpiFrame', int]:
        """Unpack frame. Returns (frame, actual_payload_len)."""
        if len(data) < 8:
            raise ValueError(f"Data too short for header: {len(data)}")

        hdr = SpiFrameHdr.unpack(data)
        payload_len = hdr.payload_len
        payload = data[8:8 + payload_len]

        return cls(hdr, payload), payload_len

# ============================================================================
# JSON Payload Helpers
# ============================================================================

def create_train_progress_json(elapsed_s: int, total_s: int, sample_count: int,
                              current_rssi_mean: float) -> str:
    """Create training progress JSON payload."""
    obj = {
        "pkt_type": "train_progress",
        "elapsed_s": elapsed_s,
        "total_s": total_s,
        "sample_count": sample_count,
        "current_rssi_mean": round(current_rssi_mean, 1)
    }
    return json.dumps(obj, separators=(',', ':'))

def create_train_done_json(base_rssi_mean: float, base_rssi_var: float,
                          sample_count: int) -> str:
    """Create training completion JSON payload."""
    obj = {
        "pkt_type": "train_done",
        "base_rssi_mean": round(base_rssi_mean, 2),
        "base_rssi_var": round(base_rssi_var, 2),
        "sample_count": sample_count,
        "entry_threshold": round(base_rssi_var * 3.0, 2)
    }
    return json.dumps(obj, separators=(',', ':'))

def create_occupancy_status_json(ts_ms: int, entry: bool, motion: bool,
                                motion_count_3s: int, rssi: float,
                                rssi_variance: float, base_rssi_mean: float,
                                base_rssi_var: float, jitter: float,
                                jitter_threshold: float,
                                occupancy_confirmed: bool,
                                req_mode_change: Optional[str] = None) -> str:
    """Create occupancy status JSON payload."""
    obj = {
        "pkt_type": "occupancy_status",
        "ts_ms": ts_ms,
        "entry": entry,
        "motion": motion,
        "motion_count_3s": motion_count_3s,
        "rssi": round(rssi, 1),
        "rssi_variance": round(rssi_variance, 2),
        "base_rssi_mean": round(base_rssi_mean, 2),
        "base_rssi_var": round(base_rssi_var, 2),
        "jitter": round(jitter, 4),
        "jitter_threshold": round(jitter_threshold, 4),
        "occupancy_confirmed": occupancy_confirmed,
        "req_mode_change": req_mode_change
    }
    return json.dumps(obj, separators=(',', ':'))

def parse_json_payload(payload: bytes) -> Dict[str, Any]:
    """Parse JSON payload bytes."""
    try:
        return json.loads(payload.decode('utf-8', errors='ignore'))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON payload: {e}")

# ============================================================================
# Training Parameters
# ============================================================================

TRAIN_WARMUP_S = 10       # time for user to leave room
TRAIN_COLLECT_S = 20      # CSI collection time (50Hz -> 1000 samples)
TRAIN_TOTAL_S = TRAIN_WARMUP_S + TRAIN_COLLECT_S

# Entry detection threshold multiplier
ENTRY_THRESHOLD_MULT = 3.0

# ============================================================================
# Occupancy FSM Parameters
# ============================================================================

# RSSI window size for entry detection (500ms @ 50Hz)
RSSI_WINDOW_SIZE = 10

# Jitter history window size (1 second @ 50Hz)
JITTER_HIST_SIZE = 20

# Motion confirmation window (3 seconds @ 50Hz)
MOTION_CONFIRM_WINDOW = 60

# Minimum motion confirmations to trigger occupancy (out of 60)
MOTION_CONFIRM_COUNT = 10

# Minimum jitter floor to filter noise
MIN_JITTER_FLOOR = 0.02

# ============================================================================
# Fall Detection Parameters
# ============================================================================

# CSI feature window (1 second @ 320Hz)
FALL_FEATURE_WINDOW = 320

# Burst detection window (2 seconds)
FALL_BURST_WINDOW = 640

# Still detection window (3 seconds)
FALL_STILL_WINDOW = 960

# Exit detection window (10 seconds @ 320Hz)
EXIT_DETECT_WINDOW = 3200

# Exit threshold: base_rssi_var * 2.0
EXIT_THRESHOLD_MULT = 2.0

__all__ = [
    'EspnowPktType', 'EspnowCmd', 'EspnowCmdPacket',
    'SpiMode', 'SpiPktType', 'RpiCmd',
    'SPI_FRAME_SIZE_SMALL', 'SPI_FRAME_SIZE_LARGE',
    'SPI_MAGIC_RX_TO_RPi', 'SPI_MAGIC_RPi_TO_RX', 'SPI_VERSION',
    'CSI_MAX_LEN',
    'SpiFrameHdr', 'RpiCmdPayload', 'CsiRawFrame', 'SpiFrame',
    'create_train_progress_json', 'create_train_done_json',
    'create_occupancy_status_json', 'parse_json_payload',
    'TRAIN_WARMUP_S', 'TRAIN_COLLECT_S', 'TRAIN_TOTAL_S',
    'ENTRY_THRESHOLD_MULT', 'RSSI_WINDOW_SIZE', 'JITTER_HIST_SIZE',
    'MOTION_CONFIRM_WINDOW', 'MOTION_CONFIRM_COUNT', 'MIN_JITTER_FLOOR',
    'FALL_FEATURE_WINDOW', 'FALL_BURST_WINDOW', 'FALL_STILL_WINDOW',
    'EXIT_DETECT_WINDOW', 'EXIT_THRESHOLD_MULT',
]
