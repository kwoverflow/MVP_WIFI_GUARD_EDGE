"""csi_recv 바이너리 프레임 프로토콜 파서.

esp32c5/csi_recv/main/app_main.c 의 csi_binary_frame_header_t 와 1:1 대응.
프레임 = 헤더(46B) + CSI 페이로드(csi_len B, 최대 612) + 체크섬(2B, 앞 전체 합의 하위 16비트).
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import numpy as np

MAGIC = 0xA55A
VERSION = 1
FRAME_TYPE_CSI = 1

HEADER_FMT = "<HHBBI6sbBBBBBBBBBBbBBBBHHIbBHBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 46
MAX_PAYLOAD = 612
MAX_FRAME = HEADER_SIZE + MAX_PAYLOAD + 2
MIN_FRAME = HEADER_SIZE + 2

_MAGIC_BYTES = struct.pack("<H", MAGIC)


@dataclass(slots=True)
class CsiFrame:
    seq: int
    mac: str
    rssi: int
    noise_floor: int
    channel: int
    timestamp_us: int  # ESP32 로컬 클럭, 2^32 랩어라운드 (unwrap은 buffer 계층에서)
    fft_gain: int
    agc_gain: int
    csi_len: int
    amps: np.ndarray  # float32 진폭, 서브캐리어 수 = csi_len // 2
    host_time: float  # 호스트 수신 시각 (time.monotonic)


class FrameParser:
    """바이트 스트림을 받아 검증된 CsiFrame 목록을 뽑아내는 재동기화 파서."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames_ok = 0
        self.checksum_errors = 0
        self.resyncs = 0

    def feed(self, data: bytes) -> list[CsiFrame]:
        self._buf.extend(data)
        out: list[CsiFrame] = []
        buf = self._buf
        while True:
            idx = buf.find(_MAGIC_BYTES)
            if idx < 0:
                # 매직 없음: 경계에 걸친 1바이트만 남기고 버림
                del buf[:-1]
                break
            if idx > 0:
                self.resyncs += 1
                del buf[:idx]
            if len(buf) < HEADER_SIZE:
                break
            frame_len = struct.unpack_from("<H", buf, 2)[0]
            if frame_len < MIN_FRAME or frame_len > MAX_FRAME:
                del buf[:2]  # 잘못된 매직 히트, 다음 후보로
                continue
            if len(buf) < frame_len:
                break
            frame = bytes(buf[:frame_len])
            del buf[:frame_len]
            parsed = self._parse_one(frame)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_one(self, frame: bytes) -> CsiFrame | None:
        frame_len = len(frame)
        checksum = struct.unpack_from("<H", frame, frame_len - 2)[0]
        if sum(frame[: frame_len - 2]) & 0xFFFF != checksum:
            self.checksum_errors += 1
            return None
        (
            _magic, _frame_len, version, frame_type, seq, mac,
            rssi, _rate, _sig_mode, _mcs, _cwb, _smoothing, _not_sounding,
            _aggregation, _stbc, _fec_coding, _sgi, noise_floor, _ampdu_cnt,
            channel, _secondary_channel, _ant, _sig_len, _rx_state,
            local_timestamp, fft_gain, agc_gain, csi_len, _first_word_invalid, _reserved,
        ) = struct.unpack_from(HEADER_FMT, frame, 0)
        if version != VERSION or frame_type != FRAME_TYPE_CSI:
            return None
        payload = frame[HEADER_SIZE : HEADER_SIZE + csi_len]
        if len(payload) != csi_len:
            return None
        # (허수, 실수) int8 인터리브 -> 진폭
        iq = np.frombuffer(payload, dtype=np.int8).astype(np.float32)
        imag, real = iq[0::2], iq[1::2]
        amps = np.sqrt(imag * imag + real * real)
        self.frames_ok += 1
        return CsiFrame(
            seq=seq,
            mac=mac.hex(":"),
            rssi=rssi,
            noise_floor=noise_floor,
            channel=channel,
            timestamp_us=local_timestamp,
            fft_gain=fft_gain,
            agc_gain=agc_gain,
            csi_len=csi_len,
            amps=amps,
            host_time=time.monotonic(),
        )
