"""WIFI-GUARD 서비스 API 엔트리포인트.

    uv run uvicorn wifiguard_api.app:app --reload --port 8000     (또는 make api)

- 접두 없음 : /, /health, /ports  (routers/health.py — 드라이런·프론트 useBackendUp 호환)
- /api/v1   : auth · facilities · devices · residents · falls · event-logs · recipients · config · account
- WS        : /ws/live  (+ GET /realtime/schema — 프론트 코드젠 입력)
- main.py(구 로컬 백엔드 계약 원본)는 마운트하지 않는다.

## 인제스트가 이 프로세스 안에서 돈다

`wifiguard_ingest` 는 라이브러리이고, 실행 주체가 이 앱의 lifespan 이다. REST 핸들러가
`LiveCache` 를 동기로 읽고 WS 가 인메모리로 팬아웃하려면 같은 주소 공간이어야 하기 때문이다.

**그래서 uvicorn 워커는 1개여야 한다.** 워커가 여럿이면 각자 별도 Kafka 컨슈머 그룹 멤버가
되어 메시지가 분산되고 캐시가 쪼개진다. `deploy/aws/wifiguard-api.service` 참조.

MQTT·Kafka 설정이 없으면 인제스트는 조용히 꺼진 채로 뜬다 — CRUD 는 실시간 경로와
무관하게 동작해야 하고, AWS 에는 아직 두 인스턴스가 없다(M4).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .errors import install_error_handlers
from .routers import auth, health

log = logging.getLogger("wifiguard_api")

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """인제스트 기동·정지. 실패해도 API 는 떠야 한다."""
    service = None
    push_worker = None
    try:
        from wifiguard_ingest import start_all

        service = start_all(loop=asyncio.get_running_loop())
        app.state.ingest = service
    except ImportError:
        log.info("wifiguard_ingest 미설치 — 실시간 경로 없이 기동한다")
        app.state.ingest = None
    except Exception:
        if os.getenv("LOCAL_EDGE_TOKEN"):
            raise  # Pi5 profile must not silently start without local ingestion.
        # 브로커가 죽었다고 CRUD 까지 못 쓰게 되면 안 된다.
        log.exception("인제스트 기동 실패 — 실시간 경로 없이 계속한다")
        app.state.ingest = None
    try:
        from wifiguard_notify.mobile import test_push_enabled as mobile_push_enabled
        if mobile_push_enabled():
            from wifiguard_notify.mobile import MobilePushWorker, FcmTransport
            from wifiguard_db.engine import get_session
            push_worker = MobilePushWorker(get_session, FcmTransport())
            push_worker.start()
        yield
    finally:
        if push_worker is not None:
            push_worker.stop()
        if service is not None:
            try:
                from wifiguard_ingest.service import stop_all
                stop_all()
            except Exception:
                log.exception("인제스트 정지 중 예외")


def create_app() -> FastAPI:
    settings.validate_security()
    if os.getenv("LOCAL_EDGE_TOKEN") and (settings.jwt_secret_bytes < 32 or settings.jwt_secret_is_default or "REPLACE" in settings.jwt_secret):
        raise RuntimeError("Pi5 local mode requires a private JWT_SECRET of at least 32 bytes")
    app = FastAPI(
        title="WIFI-GUARD API",
        version="0.1.0",
        description="HOME/FACILITY 서비스 API — 계정·시설·기기·거주자·낙상 이력·이벤트 로그·수신자·설정 + 실시간 재실(/ws/live)",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    if settings.jwt_secret_is_default:
        log.warning("JWT_SECRET 이 기본값(changeme)입니다 — 개발 환경 외에서는 반드시 교체하세요.")

    app.include_router(health.router)
    from .local_edge import router as local_edge_router
    app.include_router(local_edge_router)
    from .routers.local_control import router as control_router, internal_router
    app.include_router(control_router, prefix=API_PREFIX)
    app.include_router(internal_router)
    from .routers.pc_simulation import router as pc_simulation_router
    app.include_router(pc_simulation_router, prefix=API_PREFIX)

    try:
        from .realtime import router as realtime_router

        app.include_router(realtime_router)
    except ImportError:  # wifiguard_ingest 미설치 환경
        log.info("realtime 라우터를 마운트하지 않는다 (wifiguard_ingest 없음)")

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(auth.router)
    for name in ("facilities", "devices", "residents", "falls", "event_logs", "recipients", "config", "account", "training", "mobile"):
        try:
            module = __import__(f"wifiguard_api.routers.{name}", fromlist=["router"])
        except ModuleNotFoundError:
            continue  # M3 에서 추가되는 라우터 — 없으면 건너뛴다
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
