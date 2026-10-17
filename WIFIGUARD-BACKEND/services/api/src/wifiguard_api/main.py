"""CSI-Guard 로컬 백엔드 (단계 1-3 + 단계 5: 시리얼 통신 + 실시간 스트림 + 모델 추론 + 푸시 알림).

실행:
    python main.py                      # 포트 자동 탐지, http 127.0.0.1:8000
    python main.py --port /dev/cu.usbmodemXXXX --http-port 8000
    python main.py --no-model           # 추론 없이 수신/스트림만
    python main.py --ntfy-topic <토픽>  # FALL 확정 시 ntfy.sh 푸시 알림 (환경변수 NTFY_TOPIC도 가능)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from csi.buffer import RingBuffer
from csi.serial_reader import SerialReader
from detector import FallDetector
from notifier import DEFAULT_SERVER, NtfyNotifier
from onboarding import CalibrationState, run_calibration
from presence import PresenceConfig
from presence_loop import PresenceLoop

WS_PUSH_HZ = 10

ring = RingBuffer(max_seconds=30.0, nominal_hz=200.0)
reader: SerialReader | None = None
detector: FallDetector | None = None
detector_error: str | None = None
notifiers: dict[str, NtfyNotifier] = {}
presence_loop: PresenceLoop | None = None

# 재실(움직임/Wander) 감지 설정과 온보딩 캘리브레이션 상태 — 낙상 DL 모델과 무관한
# 별도 인메모리 상태(백엔드 재시작 시 초기화, 기존 PipelineConfig류와 동일한 설계).
presence_config = PresenceConfig()
calibration_state = CalibrationState()
calibration_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="calibration")
calibration_task: asyncio.Task | None = None

app = FastAPI(title="CSI-Guard Local Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)


def start_reader(port: str | None = None, baud: int = 921600, preferred_serial: str | None = None) -> None:
    global reader
    if reader is not None and reader.running and reader.port == port and reader.baud == baud:
        return
    old_reader = reader
    reader = None
    if old_reader is not None:
        old_reader.stop()
        old_reader.join(timeout=2.0)
    reader = SerialReader(ring, port=port, baud=baud, preferred_serial=preferred_serial)
    reader.start()


def stop_reader() -> None:
    global reader
    if reader is None:
        return
    old_reader = reader
    reader = None
    old_reader.stop()
    old_reader.join(timeout=2.0)


def wait_for_reader_connected(timeout_s: float = 3.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if reader is not None and reader.running:
            return True
        time.sleep(0.1)
    return False


@app.get("/")
def root() -> dict:
    return {"service": "csi-guard-backend", "stage": "1-2 serial + stream"}


@app.get("/ports")
def list_ports() -> dict:
    """연결 가능한 시리얼 포트 목록 (장치 설정 화면의 포트 선택용)."""
    from serial.tools import list_ports as lp

    ports = []
    for p in lp.comports():
        name = f"{p.device} {p.description or ''}"
        if "Bluetooth" in name or "debug" in name:
            continue
        ports.append(
            {
                "device": p.device,
                "description": p.description,
                "serial_number": p.serial_number,
                "active": reader is not None and reader.port == p.device,
            }
        )
    return {"ports": ports}


@app.get("/monitor/status")
def monitor_status() -> dict:
    return {
        "serial": reader.status() if reader else None,
        "buffer": ring.stats(),
        "detect": detector.status() if detector else {"enabled": False, "reason": detector_error},
        "presence": presence_loop.status() if presence_loop else {"enabled": False},
        "notify": {
            "enabled": len(notifiers) > 0,
            "count": len(notifiers),
            "reason": None if notifiers else "등록된 ntfy 수신자 없음",
            "recipients": [n.status() for n in notifiers.values()],
        },
        "server_time": time.time(),
    }


@app.post("/notify/test")
def notify_test() -> dict:
    """등록된 모든 ntfy 수신자에게 테스트 발송."""
    if not notifiers:
        return {"ok": False, "reason": "등록된 ntfy 수신자 없음"}
    for n in notifiers.values():
        n.notify_test()
    return {"ok": True, "count": len(notifiers)}


@app.get("/notify/recipients")
def list_notify_recipients() -> dict:
    return {"recipients": [n.status() for n in notifiers.values()]}


@app.post("/notify/recipients")
def add_notify_recipient(payload: dict = Body({})) -> dict:
    topic = payload.get("topic")
    if not topic or not isinstance(topic, str):
        raise HTTPException(status_code=400, detail="topic은 비어있지 않은 문자열이어야 합니다")
    server = payload.get("server") or DEFAULT_SERVER
    name = payload.get("name")
    notify_fall_enabled = payload.get("notify_fall_enabled", True)
    n = add_notifier(topic=topic, server=server, name=name, notify_fall_enabled=notify_fall_enabled)
    return n.status()

@app.patch("/notify/recipients/{recipient_id}")
def update_notify_recipient(recipient_id: str, payload: dict = Body({})) -> dict:
    n = notifiers.get(recipient_id)
    if n is None:
        raise HTTPException(status_code=404, detail="recipient not found")
    if "notify_fall_enabled" in payload:
        n.notify_fall_enabled = bool(payload["notify_fall_enabled"])
    return n.status()


@app.delete("/notify/recipients/{recipient_id}")
def delete_notify_recipient(recipient_id: str) -> dict:
    n = notifiers.pop(recipient_id, None)
    if n is None:
        raise HTTPException(status_code=404, detail="recipient not found")
    n.stop()
    n.join(timeout=2.0)
    return {"status": "deleted"}


@app.post("/notify/recipients/{recipient_id}/test")
def test_notify_recipient(recipient_id: str) -> dict:
    n = notifiers.get(recipient_id)
    if n is None:
        raise HTTPException(status_code=404, detail="recipient not found")
    n.notify_test()
    return {"ok": True}


@app.post("/monitor/start")
def monitor_start(payload: dict = Body({})) -> dict:
    port = payload.get("port")
    baud = payload.get("baud", 921600)
    preferred_serial = payload.get("preferred_serial")
    start_reader(port=port, baud=baud, preferred_serial=preferred_serial)
    connected = wait_for_reader_connected(3.0)
    return {"status": "started", "port": reader.port if reader else None, "connected": connected}


@app.post("/monitor/stop")
def monitor_stop() -> dict:
    stop_reader()
    return {"status": "stopped"}


@app.post("/onboarding/calibrate/start")
async def onboarding_calibrate_start(payload: dict = Body({})) -> dict:
    """온보딩 3단계: 캘리브레이션을 백그라운드로 시작 (leaving/waiting_ack/waiting_agc/
    measuring 4단계, 총 약 61초 — 실제 소요시간 그대로, 압축하지 않음)."""
    global calibration_task
    port = payload.get("port")
    baud = payload.get("baud", 921600)
    preferred_serial = payload.get("preferred_serial")
    if reader is None or not reader.running:
        start_reader(port=port, baud=baud, preferred_serial=preferred_serial)
        if not wait_for_reader_connected(3.0):
            raise HTTPException(status_code=400, detail="device not connected")
    if calibration_state.phase not in ("idle", "done", "error"):
        raise HTTPException(status_code=400, detail="calibration already in progress")
    calibration_task = asyncio.create_task(
        run_calibration(reader, presence_config, calibration_state, calibration_executor)
    )
    return {"status": "started"}


@app.get("/onboarding/calibrate/status")
def onboarding_calibrate_status() -> dict:
    """캘리브레이션 진행상황 폴링 (권장: 1초 간격)."""
    calib = calibration_state
    now = time.time()
    elapsed_s = (now - calib.started_at) if calib.started_at is not None else None
    phase_elapsed_s = (now - calib.phase_started_at) if calib.phase_started_at is not None else None
    return {
        "phase": calib.phase,
        "elapsed_s": round(elapsed_s, 2) if elapsed_s is not None else None,
        "phase_elapsed_s": round(phase_elapsed_s, 2) if phase_elapsed_s is not None else None,
        "agc_duration_s": round(calib.agc_duration_s, 2) if calib.agc_duration_s is not None else None,
        "presence_mv_threshold": calib.presence_mv_threshold,
        "wander_baseline": calib.wander_baseline,
        "error": calib.error,
    }


@app.get("/monitor/detect")
def monitor_detect() -> dict:
    """낙상 판정 상태와 최근 60초 확률 히스토리."""
    if detector is None:
        return {"enabled": False, "reason": detector_error}
    return {**detector.status(), "history": detector.history()}


@app.get("/monitor/window")
def monitor_window(seconds: float = 3.0) -> dict:
    times, amps = ring.window(seconds)
    if times.size == 0:
        return {"frames": 0, "seconds": seconds}
    return {
        "frames": int(times.shape[0]),
        "seconds": seconds,
        "span_sec": round(float(times[-1] - times[0]), 3),
        "subcarriers": int(amps.shape[1]),
        "amp_mean": round(float(amps.mean()), 3),
        "amp_std": round(float(amps.std()), 3),
    }


@app.get("/presence/config")
def get_presence_config() -> dict:
    return asdict(presence_config)


@app.post("/presence/config")
def update_presence_config(payload: dict = Body({})) -> dict:
    """재실/움직임(MV·WANDER) 파라미터의 실시간 조정 — presence_loop.py가 매
    0.25초 tick마다 presence_config를 그대로 다시 읽으므로, 여기서 바꾸면
    재시작 없이 곧바로 반영된다. 인메모리 전용(백엔드 재시작 시 초기화)."""
    if (v := payload.get("presence_mv_threshold")) is not None:
        v = float(v)
        if v <= 0:
            raise HTTPException(status_code=400, detail="presence_mv_threshold must be > 0")
        presence_config.presence_mv_threshold = v
    if (v := payload.get("wander_ratio_threshold")) is not None:
        v = float(v)
        if v <= 0:
            raise HTTPException(status_code=400, detail="wander_ratio_threshold must be > 0")
        presence_config.wander_ratio_threshold = v
    if (v := payload.get("presence_timeout_s")) is not None:
        v = float(v)
        if v < 0:
            raise HTTPException(status_code=400, detail="presence_timeout_s must be >= 0")
        presence_config.presence_timeout_s = v
    return asdict(presence_config)


@app.get("/detection/config")
def get_detection_config() -> dict:
    if detector is None:
        return {"enabled": False, "threshold": None, "cooldown_seconds": None}
    return {"enabled": True, "threshold": detector.threshold, "cooldown_seconds": detector.cooldown_seconds}


@app.post("/detection/config")
def update_detection_config(payload: dict = Body({})) -> dict:
    """DL 낙상 판정 파라미터의 실시간 조정 — threshold/cooldown_seconds는 detector
    스레드가 매 tick 다시 읽는 평범한 mutable 속성이라, 여기서 바꾸면 재시작 없이
    곧바로 반영된다. 인메모리 전용(백엔드 재시작 시 초기화)."""
    if detector is None:
        raise HTTPException(
            status_code=409, detail="낙상 탐지 모델이 비활성 상태입니다 (--no-model 또는 로드 실패)"
        )
    if (v := payload.get("threshold")) is not None:
        v = float(v)
        if not (0.0 <= v <= 1.0):
            raise HTTPException(status_code=400, detail="threshold must be within [0, 1]")
        detector.threshold = v
    if (v := payload.get("cooldown_seconds")) is not None:
        v = float(v)
        if v < 0:
            raise HTTPException(status_code=400, detail="cooldown_seconds must be >= 0")
        detector.cooldown_seconds = v
    return {"enabled": True, "threshold": detector.threshold, "cooldown_seconds": detector.cooldown_seconds}


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    """대시보드용 실시간 요약 스트림 (10Hz).

    프레임 원본이 아니라 시각화에 필요한 요약(수신률, RSSI, 진폭 통계)만 보낸다.
    """
    await ws.accept()
    try:
        while True:
            stats = ring.stats()
            serial_status = reader.status() if reader else {}
            times, amps = ring.window(0.5)
            payload = {
                "t": time.time(),
                "connected": serial_status.get("connected", False),
                "hz_1s": stats["hz_1s"],
                "rssi": stats["last_rssi"],
                "buffered_seconds": stats["buffered_seconds"],
                "amp_mean": round(float(amps.mean()), 3) if times.size else None,
                "amp_std": round(float(amps.std()), 3) if times.size else None,
            }
            # 재실/움직임(MV)은 DL 모델과 무관하게 항상 계산되므로 detector 유무와
            # 상관없이 먼저 병합 — detector가 있으면 낙상 판정 필드가 덧붙는다.
            if presence_loop is not None:
                payload.update(presence_loop.live_payload())
            if detector is not None:
                payload.update(detector.live_payload())
            await ws.send_text(json.dumps(payload))
            await asyncio.sleep(1.0 / WS_PUSH_HZ)
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await ws.close()


def start_detector(args: argparse.Namespace) -> None:
    global detector, detector_error
    if args.no_model:
        detector_error = "disabled by --no-model"
        return
    try:
        from inference import FallInferenceEngine

        engine = FallInferenceEngine(checkpoint_path=args.checkpoint, device=args.device)
        engine.warmup()
        # notify_all_fall은 호출 시점의 notifiers를 그대로 읽으므로, detector
        # 스레드 시작 이후 등록된 수신자에게도 낙상 알림이 정상적으로 간다 —
        # notifiers가 비어있는지 여부와 무관하게 항상 이 콜백을 넘긴다.
        detector = FallDetector(ring, engine, threshold=args.threshold, on_fall=notify_all_fall)
        detector.start()
    except Exception as error:
        detector_error = f"{type(error).__name__}: {error}"
        logging.getLogger("detector").exception("모델 로드 실패, 추론 비활성")


def start_presence_loop() -> None:
    """움직임(MV)/재실 감지는 DL 모델(--no-model, 체크포인트 미존재 등)과 무관하게
    시리얼 수신기가 붙어 있는 한 항상 동작해야 하므로 start_detector와 별개로,
    조건 없이 기동한다."""
    global presence_loop
    presence_loop = PresenceLoop(ring, presence_config)
    presence_loop.start()


def add_notifier(topic: str, server: str = DEFAULT_SERVER, name: str | None = None, notify_fall_enabled: bool = True) -> NtfyNotifier:
    recipient_id = uuid.uuid4().hex[:8]
    n = NtfyNotifier(recipient_id, topic, server=server, display_name=name, notify_fall_enabled=notify_fall_enabled)
    n.start()
    notifiers[recipient_id] = n
    return n


def notify_all_fall(fall_count: int, proba: float | None, at: float) -> None:
    """FallDetector의 on_fall 콜백 — 등록된 모든 수신자에게 개별 발송하되, 한
    수신자의 예외가 나머지 발송을 막지 않도록 각각 독립적으로 감싼다."""
    for n in list(notifiers.values()):
        try:
            n.notify_fall(fall_count, proba, at)
        except Exception:
            logging.getLogger("notifier").exception("낙상 알림 발송 실패: recipient=%s", n.id)


def start_notifier(args: argparse.Namespace) -> None:
    """--ntfy-topic/NTFY_TOPIC이 주어지면 초기 수신자 1명을 등록한다(하위 호환) —
    이후 POST /notify/recipients로 런타임에 더 추가할 수 있다."""
    if not args.ntfy_topic:
        logging.getLogger("notifier").info("ntfy 토픽 미설정, 시작 시 등록된 수신자 없음")
        return
    add_notifier(topic=args.ntfy_topic, server=args.ntfy_server)


def main() -> None:
    global reader
    from inference.engine import DEFAULT_CHECKPOINT
    from detector import DEFAULT_THRESHOLD

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None, help="시리얼 포트 (기본: 자동 탐지)")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--preferred-serial", default=None, help="포트 여러 개일 때 우선할 시리얼 번호 조각")
    ap.add_argument("--http-host", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT), help="모델 체크포인트 경로")
    ap.add_argument("--device", default="auto", choices=("auto", "cuda", "mps", "cpu"))
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--no-model", action="store_true", help="모델 추론 비활성 (수신/스트림만)")
    ap.add_argument(
        "--ntfy-topic",
        default=os.environ.get("NTFY_TOPIC"),
        help="ntfy 알림 토픽 (기본: 환경변수 NTFY_TOPIC, 미설정 시 알림 비활성)",
    )
    ap.add_argument(
        "--ntfy-server",
        default=os.environ.get("NTFY_SERVER", DEFAULT_SERVER),
        help="ntfy 서버 주소 (기본: https://ntfy.sh, 셀프호스트 시 변경)",
    )
    args = ap.parse_args()

    start_reader(port=args.port, baud=args.baud, preferred_serial=args.preferred_serial)
    start_notifier(args)
    start_presence_loop()
    start_detector(args)
    try:
        uvicorn.run(app, host=args.http_host, port=args.http_port, log_level="info")
    finally:
        if detector is not None:
            detector.stop()
        if presence_loop is not None:
            presence_loop.stop()
        for n in notifiers.values():
            n.stop()
        reader.stop()
        calibration_executor.shutdown(wait=False)


if __name__ == "__main__":
    main()
