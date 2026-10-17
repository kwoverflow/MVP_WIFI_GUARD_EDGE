"""테넌시 스코핑 — 시설 A/B 와 HOME 사용자 사이의 격리, 서버 발급 MQTT 토픽, SCOPE_MISMATCH."""

from __future__ import annotations

from helpers import create_device, create_resident, signup_home, signup_root


def test_devices_isolated_between_facilities_and_home(client):
    a = signup_root(client, "a@test.io", "Alpha Care")
    b = signup_root(client, "b@test.io", "Beta Care")
    h = signup_home(client, "h@test.io")

    dev_a = create_device(client, a, name="A-302")
    assert dev_a["facilityId"] == a.user["facilityId"] and dev_a["ownerUserId"] is None
    assert dev_a["mqttTopic"] == f"wifiguard/{a.user['facilityId']}/{dev_a['id']}"  # 서버 발급

    dev_h = create_device(client, h, name="거실", room="거실")
    assert dev_h["ownerUserId"] == h.user["id"] and dev_h["facilityId"] is None
    assert dev_h["mqttTopic"] == f"wifiguard/home-{h.user['id']}/{dev_h['id']}"

    # B 와 H 는 A 의 장치를 보지 못한다 (목록 미노출 + 단건 404)
    assert [d["id"] for d in client.get("/api/v1/devices", headers=b.h).json()] == []
    assert [d["id"] for d in client.get("/api/v1/devices", headers=h.h).json()] == [dev_h["id"]]
    r = client.get(f"/api/v1/devices/{dev_a['id']}", headers=b.h)
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert client.patch(f"/api/v1/devices/{dev_a['id']}", json={"name": "x"}, headers=h.h).status_code == 404
    assert client.delete(f"/api/v1/devices/{dev_a['id']}", headers=b.h).status_code == 404
    # A 는 여전히 본다
    assert client.get(f"/api/v1/devices/{dev_a['id']}", headers=a.h).json()["name"] == "A-302"


def test_client_supplied_scope_ids_are_ignored(client):
    a = signup_root(client, "a@test.io")
    b = signup_root(client, "b@test.io", "Beta Care")
    r = client.post("/api/v1/devices", json={
        "name": "x", "room": "1", "connection": "SERIAL", "serialPort": "COM1",
        "facilityId": b.user["facilityId"], "ownerUserId": b.user["id"],
    }, headers=a.h)
    assert r.status_code == 201
    assert r.json()["facilityId"] == a.user["facilityId"] and r.json()["ownerUserId"] is None


def test_cross_scope_references_rejected(client):
    a = signup_root(client, "a@test.io")
    b = signup_root(client, "b@test.io", "Beta Care")
    dev_a = create_device(client, a)
    res_a = create_resident(client, a, deviceIds=[dev_a["id"]])

    r = client.post("/api/v1/residents", json={"name": "r", "room": "1", "deviceIds": [dev_a["id"]]}, headers=b.h)
    assert r.status_code == 400 and r.json()["code"] == "SCOPE_MISMATCH"
    assert r.json()["fields"]["deviceIds"] == [dev_a["id"]]

    r = client.post("/api/v1/recipients", json={"name": "n", "role": "FAMILY", "residentId": res_a["id"]}, headers=b.h)
    assert r.status_code == 400 and r.json()["code"] == "SCOPE_MISMATCH"

    r = client.post("/api/v1/event-logs", json={"level": "INFO", "msg": "x", "residentId": res_a["id"]}, headers=b.h)
    assert r.status_code == 400 and r.json()["code"] == "SCOPE_MISMATCH"


def test_falls_and_logs_scoped(client):
    a = signup_root(client, "a@test.io")
    b = signup_root(client, "b@test.io", "Beta Care")
    dev = create_device(client, a)
    res = create_resident(client, a, name="김순옥", deviceIds=[dev["id"]])

    sim = client.post("/api/v1/falls/simulate", json={"residentId": res["id"]}, headers=a.h)
    assert sim.status_code == 201, sim.text
    fall = sim.json()
    assert fall["residentName"] == "김순옥" and fall["source"] == "SIMULATED" and fall["response"] == "PENDING"
    assert fall["deviceId"] == dev["id"] and fall["facilityId"] == a.user["facilityId"]

    assert client.get("/api/v1/falls", headers=a.h).json()["total"] == 1
    assert client.get("/api/v1/falls", headers=b.h).json()["total"] == 0
    assert client.patch(f"/api/v1/falls/{fall['id']}/response", json={"response": "DISPATCHED"}, headers=b.h).status_code == 404

    ok = client.patch(f"/api/v1/falls/{fall['id']}/response", json={"response": "DISPATCHED"}, headers=a.h)
    assert ok.status_code == 200 and ok.json()["response"] == "DISPATCHED"
    assert ok.json()["respondedBy"] == a.user["id"] and ok.json()["respondedAt"]

    # 이벤트 로그: A 에는 시뮬레이션 FALL + 응답 INFO 가 있고 B 에는 없다
    logs_a = client.get("/api/v1/event-logs", headers=a.h).json()
    assert any(l["level"] == "FALL" for l in logs_a["items"])
    assert any("알람 응답: DISPATCHED" in l["msg"] for l in logs_a["items"])
    logs_b = client.get("/api/v1/event-logs", params={"q": "알람"}, headers=b.h).json()
    assert logs_b["total"] == 0
    assert client.get("/api/v1/event-logs", params={"level": "FALL"}, headers=a.h).json()["total"] == 1
    assert client.get("/api/v1/event-logs", params={"q": "응답"}, headers=a.h).json()["total"] == 1

    # 응답 필터 + 페이지네이션
    page = client.get("/api/v1/falls", params={"response": "DISPATCHED", "limit": 1, "offset": 0}, headers=a.h).json()
    assert page["total"] == 1 and page["limit"] == 1 and len(page["items"]) == 1
    assert client.get("/api/v1/falls", params={"response": "PENDING"}, headers=a.h).json()["total"] == 0


def test_config_per_tenant(client):
    a = signup_root(client, "a@test.io")
    h = signup_home(client, "h@test.io")
    ca = client.get("/api/v1/config", headers=a.h).json()
    ch = client.get("/api/v1/config", headers=h.h).json()
    assert ca["presenceMvThreshold"] == 2.0 and ca["wanderRatioThreshold"] == 1.8 and ca["threshold"] == 0.468
    assert ca["presenceTimeoutS"] == 10.0 and ca["cooldownSeconds"] == 10.0
    body = {**ca, "presenceMvThreshold": 3.2}
    body = {k: body[k] for k in ("presenceMvThreshold", "wanderRatioThreshold", "presenceTimeoutS", "threshold", "cooldownSeconds")}
    assert client.put("/api/v1/config", json=body, headers=a.h).status_code == 200
    assert client.get("/api/v1/config", headers=a.h).json()["presenceMvThreshold"] == 3.2
    assert client.get("/api/v1/config", headers=h.h).json()["presenceMvThreshold"] == ch["presenceMvThreshold"] == 2.0
    bad = client.put("/api/v1/config", json={**body, "wanderRatioThreshold": 0.5}, headers=a.h)
    assert bad.status_code == 422 and "wanderRatioThreshold" in bad.json()["fields"]


def test_client_log_rejects_fall_level(client):
    h = signup_home(client, "h@test.io")
    r = client.post("/api/v1/event-logs", json={"level": "FALL", "msg": "x"}, headers=h.h)
    assert r.status_code == 422
    ok = client.post("/api/v1/event-logs", json={"level": "WARN", "msg": "실장치 연결 끊김"}, headers=h.h)
    assert ok.status_code == 201 and ok.json()["actorUserId"] == h.user["id"]
