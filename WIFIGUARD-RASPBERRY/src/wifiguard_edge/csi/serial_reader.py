"""수신기(csi_recv) 시리얼 연결 관리.

- 포트 자동 탐지 (OS 무관 — pyserial list_ports, 알려진 수신기 시리얼 우선)
- 2 Mbaud 연결, 끊김 감지, 1초 간격 자동 재연결
- 읽은 바이트를 FrameParser 에 넣고, 송신기 MAC 프레임만 RingBuffer 에 적재
"""

from __future__ import annotations

import threading
import time

import numpy as np
import serial
from serial.tools import list_ports

from .buffer import RingBuffer
from .protocol import FrameParser

#: 펌웨어 `csi_recv/sdkconfig.defaults` 의 CONFIG_ESP_CONSOLE_UART_BAUDRATE 와 **반드시 같아야 한다.**
#: 어긋나면 프레임이 아예 파싱되지 않는다(매직 0xA55A 미검출 → resync 무한 반복).
#:
#: 921600 → 2000000 (2026-09-10). 근거: 프레임 660B(46+612+2) × 10bit = 6,600bit 이므로
#: 921600 baud 의 상한은 약 140 fps 인데 실측 수신율이 약 167Hz 였다. 펌웨어 쪽 실측도
#: 같은 결론이다 — `csi_recv_calibrate/optimization_plan.md` 가 UART TX 바이트 루프를
#: 패킷당 ~7.16ms 로 측정했고, 167Hz 면 초당 1.2초로 이미 100%를 넘는다.
#: 2 Mbaud 에서는 패킷당 ~3.3ms, 상한 약 303 fps 로 여유가 생긴다.
DEFAULT_BAUD = 2_000_000
CSI_SEND_MAC = "1a:00:00:00:00:00"  # csi_send 의 스푸핑 MAC (수신기도 필터하지만 이중 방어)


def discover_port(preferred_serial: str | None = None) -> str | None:
    """macOS(cu.usbmodem*)/Windows(COM*)/Linux(ttyUSB*, ttyACM*) 어디서든 동작하도록
    pyserial의 list_ports.comports()로 후보를 모으고(main.py의 /ports 엔드포인트와
    동일한 방식), preferred_serial은 포트 경로가 아니라 하드웨어 시리얼 번호
    (comports()의 serial_number)로 매칭한다 -- macOS의 cu.usbmodemXXXX 처럼 포트
    이름 자체에 시리얼 번호가 박혀 있지 않은 OS(Windows의 COM10 등)에서도 특정
    장치를 지정할 수 있어야 하므로."""
    candidates = [
        p
        for p in list_ports.comports()
        if "Bluetooth" not in (p.description or "") and "debug" not in (p.description or "")
    ]
    if not candidates:
        return None
    if preferred_serial:
        for p in candidates:
            if preferred_serial in (p.serial_number or "") or preferred_serial in p.device:
                return p.device
    return sorted(candidates, key=lambda p: p.device)[0].device


class SerialReader(threading.Thread):
    def __init__(
        self,
        buffer: RingBuffer,
        port: str | None = None,
        baud: int = DEFAULT_BAUD,
        preferred_serial: str | None = None,
    ) -> None:
        super().__init__(daemon=True, name="csi-serial-reader")
        self.buffer = buffer
        self.fixed_port = port
        self.baud = baud
        self.preferred_serial = preferred_serial
        self.parser = FrameParser()
        self.connected = False
        self.port: str | None = None
        self.reconnects = 0
        self.mac_filtered = 0
        # Do not shadow threading.Thread._stop(), which join() invokes.
        self._stop_event = threading.Event()
        self._ser: serial.Serial | None = None
        self._ser_lock = threading.Lock()

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "port": self.port,
            "baud": self.baud,
            "reconnects": self.reconnects,
            "frames_ok": self.parser.frames_ok,
            "checksum_errors": self.parser.checksum_errors,
            "resyncs": self.parser.resyncs,
            "mac_filtered": self.mac_filtered,
        }

    # --- 온보딩 캘리브레이션(onboarding.run_calibration) 덕타이핑 계약 ---
    # migration.md §5: run_calibration()은 monitor가 .running/.packet_count/
    # .send_line()/.get_window() 4개만 제공하면 동작한다.

    @property
    def running(self) -> bool:
        """`.connected`의 별칭 — run_calibration()이 기대하는 속성 이름."""
        return self.connected

    @property
    def packet_count(self) -> int:
        """MAC 필터 이전, 파싱에 성공한 전체 CSI 프레임 수.

        캘리브레이션의 무음(silence) 감지는 "펌웨어가 뭔가 보내고 있는가"를 봐야
        하므로 송신기 MAC으로 걸러지기 전 값(parser.frames_ok)이 맞다 — 링버퍼에
        실제로 쌓인 프레임 수(mac 필터 이후)를 쓰면 안 된다.
        """
        return self.parser.frames_ok

    def get_window(self, seconds: float) -> tuple[np.ndarray, np.ndarray] | None:
        """캘리브레이션 베이스라인 캡처(run_calibration)용 — RingBuffer.get_window()에 위임."""
        return self.buffer.get_window(seconds)

    def send_line(self, text: str) -> bool:
        """열린 시리얼 포트로 명령 문자열을 전송(줄바꿈 추가). 연결 없으면 False.

        온보딩 캘리브레이션이 `"train"` 명령을 보낼 때 쓴다 — 명령을 그대로
        전달할 뿐이며, 그 명령을 해석하는 것은 csi_recv_calibrate 펌웨어다(백엔드는
        프로토콜을 새로 정의하지 않는다). run() 스레드가 아닌 다른 스레드/코루틴에서
        호출되므로 `_ser_lock`으로 보호한다.
        """
        with self._ser_lock:
            ser = self._ser
            if ser is None:
                return False
            try:
                ser.write((text + "\n").encode("ascii"))
                return True
            except (serial.SerialException, OSError):
                return False

    def run(self) -> None:
        while not self._stop_event.is_set():
            port = self.fixed_port or discover_port(self.preferred_serial)
            if port is None:
                self.connected = False
                self.port = None
                time.sleep(1.0)
                continue
            try:
                with serial.Serial(port, self.baud, timeout=0.5) as ser:
                    ser.reset_input_buffer()
                    self.port = port
                    self.connected = True
                    with self._ser_lock:
                        self._ser = ser
                    while not self._stop_event.is_set():
                        data = ser.read(8192)
                        if not data:
                            continue
                        for frame in self.parser.feed(data):
                            self.buffer.append(frame)
            except (serial.SerialException, OSError):
                pass
            with self._ser_lock:
                self._ser = None
            if self.connected:
                self.reconnects += 1
            self.connected = False
            self.port = None
            time.sleep(1.0)
