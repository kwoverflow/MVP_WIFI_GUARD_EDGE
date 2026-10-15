"""하드웨어 전송 계층.

SerialReader(UART)와 SpiReader(SPI)가 같은 덕타이핑 계약을 구현해
상위 파이프라인(RingBuffer 소비자 전부, run_calibration)을 무수정 재사용한다:

    running: bool          # 연결 여부
    packet_count: int      # 파싱 성공 프레임 누적
    send_line(text) -> bool
    get_window(seconds) -> tuple | None

명세 §5.1. base.py(Protocol/ABC 정의)는 아직 없다 — PORTING.md 참조.
"""
