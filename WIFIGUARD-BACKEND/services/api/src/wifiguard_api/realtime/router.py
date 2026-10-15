"""`/ws/live` — 다기기 실시간 구독.

## 프로토콜

    클라이언트 → 서버:  auth(첫 프레임, 5초 안) → subscribe / unsubscribe / ping
    서버 → 클라이언트:  hello → live* → pong / error

구 계약(`main.py:324-357`)은 기기 개념이 없는 **단일 스트림**이었다. 프론트도 모듈 전역
싱글턴 하나였고 FACILITY 다기기를 표현할 수 없었다. 그래서 구독 프레임을 새로 정의한다.

## 인증을 프레임으로 받는 이유

브라우저 WebSocket API 는 커스텀 헤더를 붙일 수 없고, 토큰을 URL 쿼리에 넣으면 프록시와
서버 접근 로그에 그대로 남는다. 그래서 연결 후 첫 프레임으로 받는다. 그 전까지는 아무
데이터도 보내지 않고, 5초 안에 오지 않으면 1008 로 닫는다.

## 스코프 밖 기기는 조용히 무시한다

구독 요청에 남의 기기 id 가 있어도 오류를 내지 않는다. "그런 기기가 있다/없다"를 알려 주면
존재 여부가 새기 때문이다. 그냥 구독 목록에서 빠진다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select
from wifiguard_contracts.realtime import (
    WS_CLOSE_UNAUTHORIZED,
    AuthFrame,
    DeviceLive,
    ErrorEvent,
    FallBlock,
    HelloEvent,
    LiveEvent,
    PingFrame,
    PongEvent,
    PresenceBlock,
    SubscribeFrame,
    UnsubscribeFrame,
)
from wifiguard_db.engine import get_session
from wifiguard_db.models import Device, User

from ..auth.jwt import AccessTokenError, decode_access_token
from ..routers.devices import scope_tenant_id
from ..deps import Scope

log = logging.getLogger("wifiguard_api.realtime")

router = APIRouter()

AUTH_TIMEOUT_S = 5.0
#: 여러 갱신을 모아 보내는 주기. 엣지는 4Hz 지만 기기가 늘면 프레임 수가 곱으로 늘어난다.
FLUSH_INTERVAL_S = 0.1


def _scope_from_token(token: str) -> Scope | None:
    """WS 는 Depends 를 쓸 수 없어 직접 해석한다. 규칙은 `deps.get_scope` 와 같다."""
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (AccessTokenError, KeyError, ValueError):
        return None
    with get_session() as db:
        user = db.get(User, user_id)
        if user is None:  # 제거된 멤버 — access 잔여와 무관하게 차단
            return None
        return Scope(
            user_id=user.id, service=user.service, role=user.role,
            facility_id=user.facility_id, user=user,
        )


def _scope_device_ids(scope: Scope) -> list[uuid.UUID]:
    from ..deps import scope_filter

    with get_session() as db:
        rows = db.execute(select(Device.id).where(scope_filter(Device, scope))).scalars()
        return list(rows)


async def _send_error(websocket: WebSocket, code: str, detail: str) -> None:
    await websocket.send_text(ErrorEvent(code=code, detail=detail).model_dump_json())


def _to_device_live(state, offline_after_s: float) -> DeviceLive:
    """`LiveCache.DeviceState` → 계약 모델.

    `presence`/`fall` 이 없으면 None 으로 남긴다 — 프론트가 "감지 미동작"으로 표시해야 하고,
    그것은 "이상 없음"과 다른 상태다.
    """
    presence = None
    if state.presence and state.presence.get("state"):
        presence = PresenceBlock(**state.presence, updated_at=state.presence_at)
    fall = None
    if state.fall:
        fall = FallBlock(**state.fall, updated_at=state.fall_at)
    return DeviceLive(
        device_id=state.device_id,
        online=state.online(offline_after_s),
        last_seen_at=state.last_seen_at,
        link=state.link,
        presence=presence,
        fall=fall,
    )


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    from wifiguard_ingest.cache import Subscription
    from wifiguard_ingest.service import get_service

    await websocket.accept()
    service = get_service()
    if service is None:
        await websocket.send_text(
            ErrorEvent(code="REALTIME_DISABLED", detail="실시간 경로가 기동되지 않았습니다.").model_dump_json()
        )
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    # ── 1) 인증 ────────────────────────────────────────────────────
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_S)
        frame = AuthFrame.model_validate_json(raw)
    except (TimeoutError, asyncio.TimeoutError, ValidationError, WebSocketDisconnect):
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    scope = await asyncio.to_thread(_scope_from_token, frame.token)
    if scope is None:
        await websocket.send_text(
            ErrorEvent(code="UNAUTHORIZED", detail="토큰이 유효하지 않습니다.").model_dump_json()
        )
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    tenant_id = scope_tenant_id(scope)
    allowed = set(await asyncio.to_thread(_scope_device_ids, scope))
    await websocket.send_text(
        HelloEvent(
            tenant_id=tenant_id,
            user_id=scope.user_id,
            device_ids=sorted(allowed, key=str),
            server_time=datetime.now(UTC),
        ).model_dump_json()
    )

    # ── 2) 구독 ────────────────────────────────────────────────────
    sub = Subscription(tenant_id=tenant_id, device_ids=set())
    service.hub.subscribe(sub)
    log.info("ws 연결 tenant=%s user=%s 기기=%d", tenant_id, scope.user_id, len(allowed))

    async def pump() -> None:
        """허브 → 클라이언트. 100ms 마다 모아 보낸다."""
        pending: dict[uuid.UUID, object] = {}
        while True:
            state = await sub.queue.get()
            pending[state.device_id] = state
            # 같은 창에 들어온 나머지를 비운다 (기기별 최신 하나만 남는다)
            await asyncio.sleep(FLUSH_INTERVAL_S)
            while not sub.queue.empty():
                extra = sub.queue.get_nowait()
                pending[extra.device_id] = extra
            devices = [
                _to_device_live(s, service.cache.offline_after_s)
                for d, s in pending.items()
                if d in allowed
            ]
            pending.clear()
            if devices:
                await websocket.send_text(
                    LiveEvent(devices=devices, server_time=datetime.now(UTC)).model_dump_json()
                )

    async def receive() -> None:
        """클라이언트 → 서버. 구독 변경과 ping.

        `type` 을 먼저 읽고 해당 모델만 검증한다. 모델을 차례로 시도하면 어떤 프레임이
        왜 거부됐는지 알 수 없고, 오타 하나가 "알 수 없는 프레임"으로 뭉뚱그려진다.
        """
        while True:
            raw = await websocket.receive_text()
            try:
                kind = json.loads(raw).get("type")
            except (json.JSONDecodeError, AttributeError):
                await _send_error(websocket, "BAD_JSON", "JSON 이 아닙니다.")
                continue

            model = {"subscribe": SubscribeFrame, "unsubscribe": UnsubscribeFrame, "ping": PingFrame}.get(kind)
            if model is None:
                await _send_error(websocket, "BAD_FRAME", f"알 수 없는 프레임 type: {kind!r}")
                continue
            try:
                frame = model.model_validate_json(raw)
            except ValidationError as exc:
                await _send_error(websocket, "BAD_FRAME", f"{kind} 프레임 검증 실패: {exc.error_count()} 건")
                continue

            if isinstance(frame, PingFrame):
                await websocket.send_text(PongEvent(server_time=datetime.now(UTC)).model_dump_json())
            elif isinstance(frame, UnsubscribeFrame):
                # 빈 목록 = 전체 해제. 그 외는 해당 기기만 뺀다.
                sub.device_ids = (sub.device_ids - set(frame.device_ids)) if frame.device_ids else set()
            else:  # SubscribeFrame — 빈 목록 = 스코프 전체
                sub.device_ids = {d for d in frame.device_ids if d in allowed}

    # ── 3) 현재 상태를 즉시 한 번 ───────────────────────────────────
    snapshot = service.cache.for_devices(list(allowed))
    if snapshot:
        await websocket.send_text(
            LiveEvent(
                devices=[_to_device_live(s, service.cache.offline_after_s) for s in snapshot.values()],
                server_time=datetime.now(UTC),
            ).model_dump_json()
        )

    tasks = [asyncio.create_task(pump()), asyncio.create_task(receive())]
    try:
        done, pending_tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending_tasks:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.warning("ws 태스크 종료: %s", exc)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        service.hub.unsubscribe(sub)
        log.info("ws 종료 tenant=%s (폐기 %d)", tenant_id, sub.dropped)


@router.get("/realtime/schema", tags=["realtime"], summary="/ws/live 프레임 JSON Schema")
def realtime_schema() -> dict:
    """프론트 코드젠 입력. OpenAPI 가 WebSocket 을 표현하지 못하므로 따로 낸다.

    `tools/export_openapi.py` 가 만드는 `realtime.schema.json` 과 같은 내용이다.
    """
    from pydantic import TypeAdapter
    from wifiguard_contracts import realtime as rt

    server = TypeAdapter(rt.ServerEvent).json_schema(ref_template="#/$defs/{model}")
    client = TypeAdapter(rt.ClientFrame).json_schema(ref_template="#/$defs/{model}")
    defs = {**server.pop("$defs", {}), **client.pop("$defs", {})}
    return {
        "protocolVersion": rt.PROTOCOL_VERSION,
        "$defs": defs,
        "serverEvent": server,
        "clientFrame": client,
    }
