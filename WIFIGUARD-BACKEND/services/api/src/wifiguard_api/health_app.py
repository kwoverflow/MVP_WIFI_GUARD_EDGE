"""헬스 전용 앱 — 클라우드 드라이런/EC2 user-data 호환 엔트리 (`uvicorn wifiguard_api.health_app:app`).

실제 라우트는 routers/health.py 에 있다. 서비스 API 전체는 `wifiguard_api.app:app` 을 쓴다.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers.health import router

app = FastAPI(title="WIFI-GUARD API (health)", version="0.0.2")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.cors_origin_regex,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
