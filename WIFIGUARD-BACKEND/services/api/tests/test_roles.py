"""역할 게이트 — ROOT / MEMBER / HOME USER."""

from __future__ import annotations

import re

from helpers import invite_code, signup_home, signup_member, signup_root

CFG = {"presenceMvThreshold": 2.0, "wanderRatioThreshold": 1.8, "presenceTimeoutS": 10, "threshold": 0.468, "cooldownSeconds": 10}


def test_member_cannot_edit_config_but_root_and_home_can(client):
    root = signup_root(client, "root@test.io")
    member = signup_member(client, "m@test.io", invite_code(client, root))
    home = signup_home(client, "h@test.io")

    r = client.put("/api/v1/config", json=CFG, headers=member.h)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    assert client.get("/api/v1/config", headers=member.h).status_code == 200  # 읽기는 가능
    assert client.put("/api/v1/config", json=CFG, headers=root.h).status_code == 200
    assert client.put("/api/v1/config", json=CFG, headers=home.h).status_code == 200


def test_facility_routes_role_gates(client):
    root = signup_root(client, "root@test.io")
    member = signup_member(client, "m@test.io", invite_code(client, root))
    home = signup_home(client, "h@test.io")

    assert client.get("/api/v1/facilities/me", headers=home.h).status_code == 403
    assert client.get("/api/v1/facilities/me", headers=member.h).status_code == 200
    assert client.get("/api/v1/facilities/me/members", headers=member.h).status_code == 403
    assert client.post("/api/v1/facilities/me/invite-code", headers=member.h).status_code == 403
    assert client.patch("/api/v1/facilities/me", json={"name": "x"}, headers=member.h).status_code == 403

    members = client.get("/api/v1/facilities/me/members", headers=root.h).json()
    assert {m["role"] for m in members} == {"ROOT", "MEMBER"} and len(members) == 2


def test_root_removes_member_and_cannot_remove_self(client):
    root = signup_root(client, "root@test.io")
    member = signup_member(client, "m@test.io", invite_code(client, root))

    self_del = client.delete(f"/api/v1/facilities/me/members/{root.user['id']}", headers=root.h)
    assert self_del.status_code == 400 and self_del.json()["code"] == "CANNOT_REMOVE_ROOT"

    assert client.delete(f"/api/v1/facilities/me/members/{member.user['id']}", headers=root.h).status_code == 204
    # 제거된 멤버는 남은 access 로도 즉시 차단, refresh 도 무효
    r = client.get("/api/v1/auth/me", headers=member.h)
    assert r.status_code == 401 and r.json()["code"] == "USER_NOT_FOUND"
    assert client.post("/api/v1/auth/refresh", json={"refreshToken": member.refresh}).status_code == 401
    assert client.delete(f"/api/v1/facilities/me/members/{member.user['id']}", headers=root.h).status_code == 404


def test_regenerate_invite_code(client):
    root = signup_root(client, "root@test.io", "Gangnam Care")
    old = invite_code(client, root)
    r = client.post("/api/v1/facilities/me/invite-code", headers=root.h)
    assert r.status_code == 200
    new = r.json()["inviteCode"]
    assert new != old and re.fullmatch(r"[A-Z]{2,3}-\d{4}", new)
    # 옛 코드로는 가입 불가
    bad = client.post("/api/v1/auth/signup", json={
        "email": "late@test.io", "password": "password8", "name": "x", "service": "FACILITY",
        "facilityMode": "MEMBER", "inviteCode": old,
    })
    assert bad.status_code == 400
