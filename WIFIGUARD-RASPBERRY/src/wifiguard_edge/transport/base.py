"""전송 계층 계약 (Protocol) + 팩토리.

**ABC 가 아니라 Protocol 인 이유**: `csi.serial_reader.SerialReader` 가 이미 이 계약을
전부 만족한다(`running`/`packet_count`/`send_line`/`get_window` + `start`/`stop`/`status`).
ABC 로 만들면 그 클래스를 상속하도록 고쳐야 하는데, 지금 유일하게 동작하는 실기 경로를
건드리는 셈이다. Protocol 은 기존 클래스를 한 줄도 고치지 않고 타입 검사만 붙인다.

상위 파이프라인(`PresenceLoop`, `FeatureLoop`, `run_calibration`)은 이 계약만 알면 되고,
UART/SPI/재생 중 무엇이 뒤에 있는지 몰라도 된다. SPI 전환(D2 후속)이 상위 코드를
건드리지 않게 하는 것이 이 파일의 목적이다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

    from ..csi.buffer import RingBuffer

TransportKind = Literal["serial", "spi", "replay"]
"""`wifiguard_contracts.mqtt.TransportKind` 와 같은 값이어야 한다 — 텔레메트리에 실려 나간다.
`"uart"` 가 아니라 `"serial"` 인 것은 config 의 `[transport] kind` 값과 맞추기 위함이다."""


@runtime_checkable
class CsiSource(Protocol):
    """CSI 를 받아 RingBuffer 에 적재하는 것.

    `run_calibration()` 이 요구하는 4종(`running`/`packet_count`/`send_line`/`get_window`)에
    스레드 수명(`start`/`stop`)과 진단(`status`)을 더한 것이다.
    """

    # --- 수명 ---
    def start(self) -> None:
        """수신 스레드 기동. `threading.Thread.start()` 로 충족된다."""

    def stop(self) -> None:
        """정지 요청(비블로킹)."""

    def join(self, timeout: float | None = None) -> None:
        """스레드 종료 대기."""

    # --- 상태 ---
    @property
    def running(self) -> bool:
        """수신기와 실제로 연결되어 있는가. 스레드 생존 여부가 아니다."""

    @property
    def packet_count(self) -> int:
        """파싱에 성공한 누적 프레임 수 — **MAC 필터 이전** 값.

        캘리브레이션의 침묵 감지가 "펌웨어가 무엇이든 보내고 있는가"를 봐야 하므로
        링버퍼 적재 수가 아니라 파서 성공 수여야 한다 (serial_reader.py:101-107).
        """

    def status(self) -> dict[str, Any]:
        """`wifiguard_contracts.mqtt.LinkStats` 로 옮겨질 진단 dict."""

    # --- 데이터 ---
    def get_window(self, seconds: float) -> tuple[np.ndarray, np.ndarray] | None:
        """최근 `seconds` 구간의 `(times_s, amplitude)`. 데이터가 모자라면 None."""

    def send_line(self, text: str) -> bool:
        """수신기로 명령 한 줄 전송. 연결이 없으면 False.

        캘리브레이션이 `"train"` 을 보낼 때 쓴다. 명령을 해석하는 것은 펌웨어다.
        """


def create_source(kind: TransportKind, buffer: RingBuffer, config: Any) -> CsiSource:
    """설정에 맞는 CsiSource 를 만든다. **import 는 여기서만 지연 수행된다.**

    `gpiod`/Linux `fcntl` 은 개발 PC 에 없을 수 있다. 모듈 최상단에서 하드웨어 전용
    의존성을 import 하면 사용하지 않는 전송 경로 때문에 프로세스가 뜨지 않으므로,
    선택된 구현만 여기서 지연 import 한다.
    """
    if kind == "serial":
        from ..csi.serial_reader import SerialReader

        return SerialReader(
            buffer=buffer,
            port=config.serial_port or None,
            baud=config.baudrate,
        )
    if kind == "replay":
        from .replay_source import ReplaySource

        return ReplaySource(
            buffer=buffer,
            source=config.replay_source,
            speed=config.replay_speed,
        )
    if kind == "spi":
        from .wgsp_source import WgspBatch8Source

        return WgspBatch8Source(buffer=buffer, config=config)
    raise ValueError(f"알 수 없는 transport kind: {kind!r}")
