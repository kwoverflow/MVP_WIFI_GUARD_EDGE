"""Authenticated Pi5 network GUI; privileges stay in the Unix-socket broker."""
from __future__ import annotations

import json
import os
import socket
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from ..deps import Scope, add_log, get_db, get_scope
from ..errors import ApiError, forbidden
from . import local_control

router = APIRouter(prefix="/local-edge/network", tags=["local-network"])
SOCKET = "/run/wifiguard-network/control.sock"
ACTIVE = {"connecting", "awaiting_confirmation", "committing", "rolling_back"}


def enabled():
    return local_control.enabled() and os.getenv("PI5_WIFI_ENABLED") == "1"


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ssid: str = Field(min_length=1, max_length=32)
    password: SecretStr = Field(min_length=8, max_length=64)
    security: Literal["wpa-psk", "sae"] = "wpa-psk"
    hidden: bool = False


class TransactionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID


class InterfaceOut(BaseModel):
    name: str
    addresses: list[str]
    connected: bool


class TransactionOut(BaseModel):
    id: UUID
    phase: Literal["connecting", "awaiting_confirmation", "committing", "committed", "rolling_back", "rolled_back"]
    deadline: float
    error: str | None = None


class StatusOut(BaseModel):
    enabled: bool
    available: bool = False
    secure_credentials_transport: bool = False
    interfaces: list[InterfaceOut] = Field(default_factory=list)
    ssid: str | None = None
    autoconnect: bool = False
    edge_stopped: bool = False
    updated_at_unix: float | None = None
    transaction: TransactionOut | None = None


class NetworkOut(BaseModel):
    ssid: str
    signal: int
    security: Literal["wpa-psk", "sae", "unsupported"]


class ScanOut(BaseModel):
    networks: list[NetworkOut]


class ChangeOut(BaseModel):
    transaction: TransactionOut | None


def broker(payload):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(40)
            conn.connect(SOCKET)
            conn.sendall(json.dumps(payload).encode() + b"\n")
            with conn.makefile("rb") as stream:
                raw = stream.readline(65537)
            if len(raw) > 65536 or not raw.endswith(b"\n"):
                raise ValueError("invalid broker response")
            response = json.loads(raw)
    except (OSError, ValueError, AttributeError):
        raise ApiError(503, "WIFI_AGENT_OFFLINE", "Wi-Fi 설정 서비스에 연결할 수 없습니다.") from None
    if not response.get("ok"):
        codes = {"STOP_EDGE_FIRST": (409, "센서 모니터링을 먼저 중지하세요."),
                 "CONNECTION_PENDING": (409, "진행 중인 연결을 먼저 확인하거나 되돌리세요."),
                 "STALE_TRANSACTION": (409, "연결 확인 시간이 지났습니다. 상태를 새로 확인하세요."),
                 "SCAN_RATE_LIMITED": (429, "잠시 후 다시 검색하세요."),
                 "INVALID_SSID": (422, "Wi-Fi 이름은 UTF-8 기준 1~32바이트여야 합니다."),
                 "INVALID_PASSWORD": (422, "공유기 비밀번호 형식을 확인하세요."),
                 "INVALID_SECURITY": (422, "WPA2 또는 WPA3 개인용 Wi-Fi만 지원합니다.")}
        code = response.get("error")
        status, detail = codes.get(code, (409, "Wi-Fi 작업을 완료하지 못했습니다. 기존 연결 상태를 확인하세요."))
        raise ApiError(status, code if code in codes else "WIFI_OPERATION_FAILED", detail)
    return response["data"]


def secure_transport(request):
    # Set/overwritten by the local nginx location, never trust X-Forwarded-Proto.
    return bool(request.client and request.client.host in ("127.0.0.1", "::1")
                and request.headers.get("x-wifiguard-network-transport") in ("https", "loopback"))


def owner(db, scope):
    if not enabled():
        raise ApiError(404, "WIFI_DISABLED", "이 설치에는 Pi5 Wi-Fi 설정이 활성화되지 않았습니다.")
    if not scope.can_edit_config():
        raise forbidden()
    dev = local_control.local_device(db, scope)
    if dev is None:
        raise ApiError(409, "LOCAL_DEVICE_NOT_BOUND", "먼저 Pi5 수신기를 계정에 연결하세요.")
    return dev


@router.get("/status", response_model=StatusOut)
def status(request: Request, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    if not enabled():
        return StatusOut(enabled=False)
    owner(db, scope)
    try:
        value = broker({"action": "status"})
    except ApiError:
        return StatusOut(enabled=True, secure_credentials_transport=secure_transport(request))
    return StatusOut(**value, enabled=True, available=True, secure_credentials_transport=secure_transport(request))


@router.post("/scan", response_model=ScanOut)
def scan(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    owner(db, scope)
    return broker({"action": "scan"})


@router.post("/connect", response_model=ChangeOut)
def connect(body: ConnectIn, request: Request, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    dev = owner(db, scope)
    if not secure_transport(request):
        raise ApiError(403, "SECURE_TRANSPORT_REQUIRED", "비밀번호 입력은 HTTPS 또는 보안 SSH 연결을 사용하세요.")
    # Root broker validates again; SecretStr prevents accidental repr disclosure.
    result = broker({"action": "connect", "ssid": body.ssid, "password": body.password.get_secret_value(),
                     "security": body.security, "hidden": body.hidden})
    add_log(db, scope, "INFO", "Pi5 Wi-Fi 임시 연결 요청", device_id=dev.id)
    return result


@router.post("/confirm", response_model=ChangeOut)
def confirm(body: TransactionIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    dev = owner(db, scope)
    result = broker({"action": "confirm", "id": str(body.id)})
    add_log(db, scope, "INFO", "Pi5 Wi-Fi 연결 저장 · 자동 연결 설정", device_id=dev.id)
    return result


@router.post("/rollback", response_model=ChangeOut)
def rollback(body: TransactionIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    dev = owner(db, scope)
    result = broker({"action": "rollback", "id": str(body.id)})
    add_log(db, scope, "INFO", "Pi5 Wi-Fi 임시 연결 되돌리기", device_id=dev.id)
    return result


def require_network_ready():
    if enabled():
        state = broker({"action": "status"})
        if (state.get("transaction") or {}).get("phase") in ACTIVE:
            raise ApiError(409, "WIFI_CONFIGURATION_PENDING", "Wi-Fi 연결을 먼저 저장하거나 되돌리세요.")
