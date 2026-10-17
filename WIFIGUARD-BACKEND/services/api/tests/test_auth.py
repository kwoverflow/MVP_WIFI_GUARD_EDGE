"""인증 흐름 — 가입(HOME/ROOT/MEMBER), 로그인, refresh 회전·재사용 감지, 로그아웃, 제거된 사용자."""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select

from wifiguard_api.config import ApiSettings
from wifiguard_db.models import Facility, RefreshToken, TenantConfig, User


def _signup_home(client, email="home@test.io", name="홈"):
    return client.post("/api/v1/auth/signup", json={
        "email": email, "password": "password8", "name": name, "service": "HOME",
    })


def _signup_root(client, email="root@test.io", facility_name="Test Care"):
    return client.post("/api/v1/auth/signup", json={
        "email": email, "password": "password8", "name": "루트", "service": "FACILITY",
        "facilityMode": "ROOT", "facilityName": facility_name,
    })


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_explicit_jwt_secret_requires_32_bytes():
    configured = ApiSettings("too-short", 15, 14, ".*", True)
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        configured.validate_security()


def test_signup_home_returns_tokens_and_me(client, db):
    r = _signup_home(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tokenType"] == "Bearer" and body["expiresIn"] == 15 * 60
    assert body["user"]["service"] == "HOME" and body["user"]["role"] == "USER"
    assert body["user"]["facilityId"] is None

    me = client.get("/api/v1/auth/me", headers=_auth(body["accessToken"]))
    assert me.status_code == 200
    assert me.json()["email"] == "home@test.io" and me.json()["facility"] is None
    assert me.json()["features"]["fallSimulate"] is True
    # HOME 사용자당 tenant_config 1행
    uid = uuid.UUID(body["user"]["id"])
    assert db.execute(select(TenantConfig).where(TenantConfig.owner_user_id == uid)).scalar_one()


def test_signup_facility_root_and_member(client, db):
    r = _signup_root(client)
    assert r.status_code == 201, r.text
    root = r.json()
    assert root["user"]["role"] == "ROOT" and root["user"]["facilityId"]
    me = client.get("/api/v1/auth/me", headers=_auth(root["accessToken"])).json()
    code = me["facility"]["inviteCode"]
    assert re.fullmatch(r"[A-Z]{2,3}-\d{4}", code), code
    assert code.startswith("TE-")  # "Test Care" → ASCII 앞 2글자

    # MEMBER 가입 (초대코드 소문자도 허용)
    m = client.post("/api/v1/auth/signup", json={
        "email": "member@test.io", "password": "password8", "name": "멤버", "service": "FACILITY",
        "facilityMode": "MEMBER", "inviteCode": code.lower(),
    })
    assert m.status_code == 201, m.text
    assert m.json()["user"]["role"] == "MEMBER"
    assert m.json()["user"]["facilityId"] == root["user"]["facilityId"]

    # 잘못된 코드 / 시설명 누락
    bad = client.post("/api/v1/auth/signup", json={
        "email": "x@test.io", "password": "password8", "name": "x", "service": "FACILITY",
        "facilityMode": "MEMBER", "inviteCode": "ZZ-0000",
    })
    assert bad.status_code == 400 and bad.json()["code"] == "INVALID_INVITE_CODE"
    noname = client.post("/api/v1/auth/signup", json={
        "email": "y@test.io", "password": "password8", "name": "y", "service": "FACILITY", "facilityMode": "ROOT",
    })
    assert noname.status_code == 400 and noname.json()["code"] == "FACILITY_NAME_REQUIRED"

    fac = db.execute(select(Facility)).scalar_one()
    assert str(fac.root_user_id) == root["user"]["id"]
    assert db.execute(select(TenantConfig).where(TenantConfig.facility_id == fac.id)).scalar_one()


def test_korean_facility_name_gets_random_ascii_prefix(client):
    r = _signup_root(client, email="k@test.io", facility_name="강남요양원")
    assert r.status_code == 201
    code = client.get("/api/v1/auth/me", headers=_auth(r.json()["accessToken"])).json()["facility"]["inviteCode"]
    assert re.fullmatch(r"[A-Z]{2}-\d{4}", code)


def test_duplicate_email_case_insensitive(client):
    assert _signup_home(client, email="Dup@test.io").status_code == 201
    r = _signup_home(client, email="dup@TEST.io")
    assert r.status_code == 409 and r.json()["code"] == "EMAIL_TAKEN"


def test_validation_errors(client):
    r = client.post("/api/v1/auth/signup", json={
        "email": "not-an-email", "password": "short", "name": "", "service": "HOME",
    })
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert {"email", "password", "name"} <= set(body["fields"])


def test_login_and_wrong_password(client):
    _signup_home(client)
    ok = client.post("/api/v1/auth/login", json={"email": "HOME@test.io", "password": "password8"})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == "home@test.io"
    bad = client.post("/api/v1/auth/login", json={"email": "home@test.io", "password": "nope-nope"})
    assert bad.status_code == 401 and bad.json()["code"] == "INVALID_CREDENTIALS"
    assert bad.headers.get("www-authenticate") == "Bearer"


def test_refresh_rotation_and_reuse_detection(client, db):
    first = _signup_home(client).json()
    r1 = client.post("/api/v1/auth/refresh", json={"refreshToken": first["refreshToken"]})
    assert r1.status_code == 200
    second = r1.json()
    assert second["refreshToken"] != first["refreshToken"]
    assert client.get("/api/v1/auth/me", headers=_auth(second["accessToken"])).status_code == 200

    # 옛 토큰 재사용 → 패밀리 전체 폐기
    reuse = client.post("/api/v1/auth/refresh", json={"refreshToken": first["refreshToken"]})
    assert reuse.status_code == 401 and reuse.json()["code"] == "REFRESH_REUSED"
    dead = client.post("/api/v1/auth/refresh", json={"refreshToken": second["refreshToken"]})
    assert dead.status_code == 401
    assert all(t.revoked_at is not None for t in db.execute(select(RefreshToken)).scalars())

    unknown = client.post("/api/v1/auth/refresh", json={"refreshToken": "garbage"})
    assert unknown.status_code == 401 and unknown.json()["code"] == "REFRESH_INVALID"


def test_logout_revokes_refresh(client):
    pair = _signup_home(client).json()
    r = client.post("/api/v1/auth/logout", json={"refreshToken": pair["refreshToken"]}, headers=_auth(pair["accessToken"]))
    assert r.status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refreshToken": pair["refreshToken"]}).status_code == 401


def test_removed_user_access_token_rejected(client, db):
    pair = _signup_home(client).json()
    uid = uuid.UUID(pair["user"]["id"])
    db.delete(db.get(User, uid))
    db.commit()
    r = client.get("/api/v1/auth/me", headers=_auth(pair["accessToken"]))
    assert r.status_code == 401 and r.json()["code"] == "USER_NOT_FOUND"


def test_missing_or_bad_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    r = client.get("/api/v1/auth/me", headers=_auth("not.a.jwt"))
    assert r.status_code == 401 and r.json()["code"] == "TOKEN_INVALID"


def test_health_and_ports(client):
    assert client.get("/ports").json() == {"ports": []}
    h = client.get("/health")
    assert h.status_code == 200, h.text
    assert h.json()["checks"]["database"]["ok"] is True
    assert h.json()["checks"]["database"]["schema"] == "0005"
