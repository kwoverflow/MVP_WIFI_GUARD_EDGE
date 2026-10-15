"""헬스 라우터 — `/`, `/health`, `/ports`. 접두 없음(클라우드 드라이런·프론트 useBackendUp 호환).

- database   : DATABASE_URL(wifiguard_db) — 필수
- timescaledb: TSDB_DSN 이 설정된 경우에만
- kafka      : KAFKA_BOOTSTRAP 이 설정된 경우에만 (이번 단계 미사용)
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import psycopg
from fastapi import APIRouter, Response
from sqlalchemy import text

from wifiguard_db.engine import engine

router = APIRouter(tags=["health"])
CONNECT_TIMEOUT_S = 3


def _db_check() -> dict[str, Any]:
    with engine.connect() as conn:
        version = conn.execute(text("select version()")).scalar_one()
        ext = conn.execute(
            text("select extversion from pg_extension where extname = 'timescaledb'")
        ).scalar_one_or_none()
        rev = conn.execute(text("select version_num from alembic_version")).scalar_one_or_none()
    return {"ok": True, "server": version.split(",")[0], "timescaledb": ext, "schema": rev}


def _pg_dsn_check(dsn: str) -> dict[str, Any]:
    with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_S) as conn:
        version = conn.execute("select version()").fetchone()[0]
        ext = conn.execute("select extversion from pg_extension where extname = 'timescaledb'").fetchone()
    return {"ok": True, "server": version.split(",")[0], "timescaledb": ext[0] if ext else None}


def _kafka_check(bootstrap: str) -> dict[str, Any]:
    from kafka import KafkaAdminClient

    admin = KafkaAdminClient(bootstrap_servers=bootstrap, client_id="wifiguard-health",
                             request_timeout_ms=CONNECT_TIMEOUT_S * 1000)
    try:
        topics = sorted(t for t in admin.list_topics() if not t.startswith("__"))
    finally:
        admin.close()
    return {"ok": True, "topics": topics}


def _safe(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn(*args)
    except Exception as exc:  # noqa: BLE001 — 헬스 응답에 원인을 그대로 싣는다
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


@router.get("/")
def root() -> dict[str, Any]:
    return {"service": "wifiguard-api", "stage": "service-crud", "server_time": time.time()}


def _ingest_status() -> dict[str, Any] | None:
    """인제스트 실측 상태. 없으면 None (실시간 경로 미기동).

    `checks` 와 분리한 이유: 인제스트가 꺼져 있어도 **degraded 가 아니다**. CRUD 는
    실시간 경로와 무관하게 정상이고, AWS 에는 아직 브로커·Kafka 가 없다(M4). 여기서
    503 을 내면 헬스체크에 걸려 배포가 막힌다.
    """
    try:
        from wifiguard_ingest.service import get_service
    except ImportError:
        return None
    service = get_service()
    return service.status() if service else None


@router.get("/health")
def health(response: Response) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {"database": _safe(_db_check)}
    if os.environ.get("TSDB_DSN"):
        checks["timescaledb"] = _safe(_pg_dsn_check, os.environ["TSDB_DSN"])
    if os.environ.get("KAFKA_BOOTSTRAP"):
        checks["kafka"] = _safe(_kafka_check, os.environ["KAFKA_BOOTSTRAP"])
    all_ok = all(c.get("ok") for c in checks.values())
    if not all_ok:
        response.status_code = 503
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "ingest": _ingest_status(),
    }


@router.get("/ports")
def list_ports() -> dict[str, list[dict[str, Any]]]:
    """main.py:97-115 호환 — 프론트 useBackendUp()(backend.ts:55)이 이 경로로 생존을 판정한다.

    클라우드에는 시리얼 포트가 없으므로 항상 빈 목록이다. 응답 형태 {"ports": [...]} 는 유지한다.
    """
    return {"ports": []}
