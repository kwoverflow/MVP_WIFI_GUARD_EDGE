"""`/ws/live` 프로토콜과 REST 런타임 필드.

M2 의 목표는 "재실감지 결과가 Pi 에서 화면까지 흐른다"이고, 이 파일이 그 마지막 두 구간
(캐시 → REST, 캐시 → WS)을 검증한다. 앞 구간(MQTT → Kafka → 캐시)은
`services/ingest/tests/test_bridge_and_consumer.py` 가 본다.

**안전 요구**: 캐시가 비면 필드는 `null` 이고 프론트는 "감지 미동작"으로 표시한다.
"퇴실"이나 "이상 없음"으로 단정하면 안 된다 — 그 구분이 이 계약의 핵심이다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from helpers import create_device, create_resident, signup_home, signup_root


@pytest.fixture
def service(client):
    """앱 lifespan 이 만든 인제스트 서비스. 없으면 테스트를 건너뛴다."""
    from wifiguard_ingest.service import get_service

    svc = get_service()
    if svc is None:
        pytest.skip("인제스트가 기동되지 않았다")
    return svc


def _presence(state: str = "present", **over) -> dict:
    base = dict(
        state=state, mv_current=3.1, wander_current=0.9, mv_threshold=2.0,
        wander_baseline=0.5, wander_ratio_threshold=1.8, wander_ratio=1.8,
        wander_confirmed=True, last_activity_at=1757000000.0,
        seconds_since_activity=0.25, just_changed=False,
    )
    return {**base, **over}


def _tenant_of(actor) -> str:
    user = actor.user
    return user["facilityId"] if user.get("facilityId") else f"home-{user['id']}"


# ── REST: 런타임 필드 ────────────────────────────────────────────────
def test_resident_runtime_fields_are_null_without_data(client):
    """실시간 경로에 아무것도 안 왔으면 전부 null — '감지 미동작'."""
    home = signup_home(client, "rt-null@demo.io")
    dev = create_device(client, home, connection="MQTT")
    res = create_resident(client, home, device_id=dev["id"])

    assert res["presence"] is None
    assert res["state"] is None
    assert res["mv"] is None and res["wander"] is None
    assert res["online"] is None, "'모름'이지 '연결 끊김'이 아니다"


def test_resident_runtime_fields_filled_from_cache(client, service):
    home = signup_home(client, "rt-fill@demo.io")
    dev = create_device(client, home, connection="MQTT")
    create_resident(client, home, device_id=dev["id"])

    device_id, tenant = UUID(dev["id"]), _tenant_of(home)
    service.cache.apply_presence(device_id, tenant, _presence(), datetime.now(UTC))
    service.cache.apply_telemetry(device_id, tenant, {"connected": True}, datetime.now(UTC))

    got = client.get("/api/v1/residents", headers=home.h).json()[0]
    assert got["presence"] == "PRESENT", "와이어는 소문자, REST 는 대문자"
    assert got["mv"] == 3.1
    assert got["wander"] == 0.9
    assert got["online"] is True
    assert got["lastActivityAt"] is not None
    assert got["state"] is None, "낙상은 M5 — 아직 항상 null"


def test_absent_is_not_the_same_as_unknown(client, service):
    home = signup_home(client, "rt-absent@demo.io")
    dev = create_device(client, home, connection="MQTT")
    create_resident(client, home, device_id=dev["id"])

    service.cache.apply_presence(UUID(dev["id"]), _tenant_of(home), _presence("absent"), datetime.now(UTC))
    got = client.get("/api/v1/residents", headers=home.h).json()[0]
    assert got["presence"] == "ABSENT"


def test_device_online_is_tristate(client, service):
    home = signup_home(client, "rt-dev@demo.io")
    dev = create_device(client, home, connection="MQTT")
    device_id, tenant = UUID(dev["id"]), _tenant_of(home)

    assert dev["online"] is None, "등록 직후 = 모름"

    service.cache.apply_telemetry(device_id, tenant, {"connected": True}, datetime.now(UTC))
    assert client.get(f"/api/v1/devices/{dev['id']}", headers=home.h).json()["online"] is True

    service.cache.offline_after_s = -1.0  # 유예를 즉시 만료시킨다
    try:
        assert client.get(f"/api/v1/devices/{dev['id']}", headers=home.h).json()["online"] is False
    finally:
        service.cache.offline_after_s = 15.0


# ── WS: 프로토콜 ─────────────────────────────────────────────────────
def test_ws_rejects_bad_token(client):
    with client.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "not-a-jwt"}))
        event = json.loads(ws.receive_text())
        assert event["type"] == "error" and event["code"] == "UNAUTHORIZED"


def test_ws_rejects_non_auth_first_frame(client):
    """첫 프레임은 반드시 auth 여야 한다."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_text(json.dumps({"type": "subscribe", "device_ids": []}))
            ws.receive_text()


def test_ws_hello_lists_scope_devices(client, service):
    root = signup_root(client, "rt-ws@demo.io")
    d1 = create_device(client, root, name="a", connection="MQTT")
    d2 = create_device(client, root, name="b", connection="MQTT")

    with client.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": root.access}))
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello"
        assert hello["protocol_version"] == 1
        assert set(hello["device_ids"]) == {d1["id"], d2["id"]}


def test_ws_delivers_presence_updates(client, service):
    home = signup_home(client, "rt-push@demo.io")
    dev = create_device(client, home, connection="MQTT")
    device_id, tenant = UUID(dev["id"]), _tenant_of(home)

    with client.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": home.access}))
        assert json.loads(ws.receive_text())["type"] == "hello"
        ws.send_text(json.dumps({"type": "subscribe", "device_ids": []}))

        state = service.cache.apply_presence(device_id, tenant, _presence(), datetime.now(UTC))
        service.hub.publish_from_thread(state)

        event = json.loads(ws.receive_text())
        assert event["type"] == "live"
        block = event["devices"][0]
        assert block["device_id"] == dev["id"]
        assert block["presence"]["state"] == "present"
        assert block["presence"]["seconds_since_activity"] == 0.25
        assert block["fall"] is None, "낙상 미동작은 null 로 표현된다"


def test_ws_does_not_leak_other_tenants(client, service):
    """다른 테넌트의 갱신이 새면 안 된다."""
    home = signup_home(client, "rt-a@demo.io")
    other = signup_home(client, "rt-b@demo.io")
    create_device(client, home, connection="MQTT")
    other_dev = create_device(client, other, connection="MQTT")

    with client.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": home.access}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "subscribe", "device_ids": []}))

        state = service.cache.apply_presence(
            UUID(other_dev["id"]), _tenant_of(other), _presence(), datetime.now(UTC)
        )
        service.hub.publish_from_thread(state)

        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong", "남의 live 가 먼저 오면 안 된다"


def test_ws_subscribe_ignores_out_of_scope_ids(client, service):
    """스코프 밖 id 는 오류가 아니라 조용히 빠진다 — 존재 여부를 노출하지 않는다."""
    home = signup_home(client, "rt-scope@demo.io")
    create_device(client, home, connection="MQTT")

    with client.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": home.access}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "subscribe", "device_ids": [str(uuid4())]}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"


def test_realtime_schema_is_served(client):
    """프론트 코드젠 입력. OpenAPI 가 WS 를 표현 못하므로 따로 낸다."""
    body = client.get("/realtime/schema").json()
    assert body["protocolVersion"] == 1
    assert {"HelloEvent", "LiveEvent", "AuthFrame", "SubscribeFrame"} <= set(body["$defs"])
