"""테스트 헬퍼 — 계정 생성과 인증 헤더."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Actor:
    access: str
    refresh: str
    user: dict

    @property
    def h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


def signup_home(client, email: str, name: str = "홈") -> Actor:
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": "password8", "name": name, "service": "HOME"})
    assert r.status_code == 201, r.text
    b = r.json()
    return Actor(b["accessToken"], b["refreshToken"], b["user"])


def signup_root(client, email: str, facility_name: str = "Test Care") -> Actor:
    r = client.post("/api/v1/auth/signup", json={
        "email": email, "password": "password8", "name": "루트", "service": "FACILITY",
        "facilityMode": "ROOT", "facilityName": facility_name,
    })
    assert r.status_code == 201, r.text
    b = r.json()
    return Actor(b["accessToken"], b["refreshToken"], b["user"])


def invite_code(client, actor: Actor) -> str:
    me = client.get("/api/v1/auth/me", headers=actor.h)
    assert me.status_code == 200, me.text
    return me.json()["facility"]["inviteCode"]


def signup_member(client, email: str, code: str) -> Actor:
    r = client.post("/api/v1/auth/signup", json={
        "email": email, "password": "password8", "name": "멤버", "service": "FACILITY",
        "facilityMode": "MEMBER", "inviteCode": code,
    })
    assert r.status_code == 201, r.text
    b = r.json()
    return Actor(b["accessToken"], b["refreshToken"], b["user"])


def create_device(client, actor: Actor, name: str = "d", room: str = "302", connection: str = "MQTT", **extra) -> dict:
    r = client.post("/api/v1/devices", json={"name": name, "room": room, "connection": connection, **extra}, headers=actor.h)
    assert r.status_code == 201, r.text
    return r.json()


def create_resident(client, actor: Actor, name: str = "r", room: str = "302", **extra) -> dict:
    r = client.post("/api/v1/residents", json={"name": name, "room": room, **extra}, headers=actor.h)
    assert r.status_code == 201, r.text
    return r.json()
