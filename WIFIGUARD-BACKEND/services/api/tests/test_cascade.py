"""삭제 연쇄·매핑 규칙 — 주 장치 409, 부 장치 삭제, 거주자 삭제 후 수신자/이력, 계정 변경."""

from __future__ import annotations

from helpers import create_device, create_resident, signup_home, signup_root


def test_primary_device_rules(client):
    a = signup_root(client, "a@test.io")
    d1 = create_device(client, a, name="d1")
    d2 = create_device(client, a, name="d2")
    d3 = create_device(client, a, name="d3")

    r = create_resident(client, a, name="김옥자", room="204", deviceId=d1["id"], deviceIds=[d1["id"], d2["id"]])
    assert r["deviceId"] == d1["id"] and set(r["deviceIds"]) == {d1["id"], d2["id"]}
    assert r["state"] is None and r["presence"] is None and r["online"] is None  # 감지 미동작

    # 주 장치 삭제 거부
    conflict = client.delete(f"/api/v1/devices/{d1['id']}", headers=a.h)
    assert conflict.status_code == 409 and conflict.json()["code"] == "DEVICE_IS_PRIMARY"
    assert conflict.json()["fields"]["residentIds"] == [r["id"]]
    # 부 장치 삭제 → 매핑 행만 사라진다
    assert client.delete(f"/api/v1/devices/{d2['id']}", headers=a.h).status_code == 204
    got = client.get(f"/api/v1/residents/{r['id']}", headers=a.h).json()
    assert got["deviceIds"] == [d1["id"]] and got["deviceId"] == d1["id"]

    # deviceId 가 deviceIds 밖이면 ids[0] 이 주 장치가 된다 (residents.tsx:184-192 규칙)
    p = client.patch(f"/api/v1/residents/{r['id']}", json={"deviceId": d3["id"], "deviceIds": [d1["id"]]}, headers=a.h)
    assert p.status_code == 200 and p.json()["deviceId"] == d1["id"]
    # deviceIds 만 바꾸면 기존 주 장치를 유지하되, 목록에 없으면 첫 원소로
    p = client.patch(f"/api/v1/residents/{r['id']}", json={"deviceIds": [d3["id"], d1["id"]]}, headers=a.h)
    assert p.json()["deviceId"] == d1["id"] and p.json()["deviceIds"] == [d3["id"], d1["id"]]
    p = client.patch(f"/api/v1/residents/{r['id']}", json={"deviceIds": [d3["id"]]}, headers=a.h)
    assert p.json()["deviceId"] == d3["id"]
    # 매핑 전부 제거
    p = client.patch(f"/api/v1/residents/{r['id']}", json={"deviceIds": []}, headers=a.h)
    assert p.json()["deviceId"] is None and p.json()["deviceIds"] == []


def test_resident_delete_keeps_history_and_nulls_recipient(client):
    a = signup_root(client, "a@test.io")
    d = create_device(client, a)
    r = create_resident(client, a, name="김순옥", deviceIds=[d["id"]])
    rec = client.post("/api/v1/recipients", json={
        "name": "김보호", "role": "FAMILY", "phone": "010-1234-5678", "sms": True, "residentId": r["id"],
    }, headers=a.h).json()
    fall = client.post("/api/v1/falls/simulate", json={"residentId": r["id"]}, headers=a.h).json()

    assert client.delete(f"/api/v1/residents/{r['id']}", headers=a.h).status_code == 204
    assert client.get(f"/api/v1/residents/{r['id']}", headers=a.h).status_code == 404

    recs = client.get("/api/v1/recipients", headers=a.h).json()
    assert [x["id"] for x in recs] == [rec["id"]] and recs[0]["residentId"] is None  # 공용으로 남는다
    kept = client.get(f"/api/v1/falls/{fall['id']}", headers=a.h).json()
    assert kept["residentId"] is None and kept["residentName"] == "김순옥" and kept["room"] == "302"  # 스냅샷 보존
    logs = client.get("/api/v1/event-logs", params={"q": "거주자 삭제"}, headers=a.h).json()
    assert logs["total"] == 1


def test_device_validation_and_topic_uniqueness(client):
    a = signup_root(client, "a@test.io")
    r = client.post("/api/v1/devices", json={"name": "s", "room": "1", "connection": "SERIAL"}, headers=a.h)
    assert r.status_code == 422 and "serialPort" in r.json()["fields"]
    ok = client.post("/api/v1/devices", json={"name": "s", "room": "1", "connection": "MQTT", "mqttTopic": "csi/gn/302/bed"}, headers=a.h)
    assert ok.status_code == 201
    dup = client.post("/api/v1/devices", json={"name": "s2", "room": "1", "connection": "MQTT", "mqttTopic": "csi/gn/302/bed"}, headers=a.h)
    assert dup.status_code == 409 and dup.json()["code"] == "TOPIC_TAKEN"
    # 다른 스코프에서는 같은 토픽 허용
    b = signup_root(client, "b@test.io", "Beta Care")
    assert client.post("/api/v1/devices", json={"name": "s", "room": "1", "connection": "MQTT", "mqttTopic": "csi/gn/302/bed"}, headers=b.h).status_code == 201


def test_calibration_patch_logs(client):
    h = signup_home(client, "h@test.io")
    d = create_device(client, h, name="거실 장치", room="거실", connection="SERIAL", serialPort="COM4")
    assert client.patch(f"/api/v1/devices/{d['id']}", json={"calibrating": True, "calibrationStage": "LEAVING", "calibrationProgress": 0}, headers=h.h).status_code == 200
    done = client.patch(f"/api/v1/devices/{d['id']}", json={
        "calibrating": False, "calibrationStage": "DONE", "calibrationProgress": 1,
        "presenceMvThreshold": 2.1, "wanderBaseline": 0.42,
    }, headers=h.h)
    assert done.status_code == 200 and done.json()["presenceMvThreshold"] == 2.1
    logs = client.get("/api/v1/event-logs", headers=h.h).json()["items"]
    assert any("캘리브레이션 완료" in l["msg"] for l in logs) and any("재설정 시작" in l["msg"] for l in logs)
    bad = client.patch(f"/api/v1/devices/{d['id']}", json={"calibrationStage": "NOPE"}, headers=h.h)
    assert bad.status_code == 422


def test_recipient_patch_and_test_delivery(client, monkeypatch):
    class SuccessfulNotifier:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def send_test_now(self):
            return True

    monkeypatch.setattr("wifiguard_api.routers.recipients.EmailNotifier", SuccessfulNotifier)
    monkeypatch.setattr("wifiguard_api.routers.recipients.NtfyNotifier", SuccessfulNotifier)
    h = signup_home(client, "h@test.io")
    rec = client.post("/api/v1/recipients", json={
        "name": "n", "role": "FAMILY", "push": True, "ntfyTopic": "abc123",
        "email": "caregiver@example.com", "emailEnabled": True,
    }, headers=h.h).json()
    p = client.patch(f"/api/v1/recipients/{rec['id']}", json={"sms": True, "phone": "010-0000-0000", "role": "ADMIN"}, headers=h.h)
    assert p.status_code == 200 and p.json()["sms"] is True and p.json()["role"] == "ADMIN" and p.json()["ntfyTopic"] == "abc123"
    t = client.post(f"/api/v1/recipients/{rec['id']}/test", headers=h.h)
    assert t.status_code == 200
    assert t.json()["ok"] is True
    assert [item["channel"] for item in t.json()["channels"]] == ["email", "push"]
    assert client.delete(f"/api/v1/recipients/{rec['id']}", headers=h.h).status_code == 204
    assert client.get("/api/v1/recipients", headers=h.h).json() == []


def test_recipient_requires_channel_destination(client):
    h = signup_home(client, "recipient-validation@test.io")
    missing_email = client.post(
        "/api/v1/recipients",
        json={"name": "n", "role": "FAMILY", "emailEnabled": True},
        headers=h.h,
    )
    assert missing_email.status_code == 400 and missing_email.json()["code"] == "EMAIL_REQUIRED"
    missing_topic = client.post(
        "/api/v1/recipients",
        json={"name": "n", "role": "FAMILY", "push": True},
        headers=h.h,
    )
    assert missing_topic.status_code == 400 and missing_topic.json()["code"] == "NTFY_TOPIC_REQUIRED"


def test_account_patch_and_password_change(client):
    h = signup_home(client, "h@test.io")
    other = signup_home(client, "other@test.io")
    taken = client.patch("/api/v1/account", json={"email": "OTHER@test.io"}, headers=h.h)
    assert taken.status_code == 409 and taken.json()["code"] == "EMAIL_TAKEN"
    ok = client.patch("/api/v1/account", json={"name": "새이름", "email": "new@test.io"}, headers=h.h)
    assert ok.status_code == 200 and ok.json()["name"] == "새이름" and ok.json()["email"] == "new@test.io"

    wrong = client.post("/api/v1/account/password", json={"currentPassword": "nope-nope", "newPassword": "password9"}, headers=h.h)
    assert wrong.status_code == 400 and wrong.json()["code"] == "WRONG_PASSWORD"
    changed = client.post("/api/v1/account/password", json={"currentPassword": "password8", "newPassword": "password9"}, headers=h.h)
    assert changed.status_code == 200
    # 기존 refresh 는 폐기, 새 비밀번호로 로그인
    assert client.post("/api/v1/auth/refresh", json={"refreshToken": h.refresh}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refreshToken": changed.json()["refreshToken"]}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "new@test.io", "password": "password9"}).status_code == 200
    assert other.user["email"] == "other@test.io"
