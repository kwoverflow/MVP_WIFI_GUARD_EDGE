"""Authenticated Pi5 UI control. No shell execution in the API process.

Commands are bounded, expiring files consumed by the fixed-unit control agent.
The original cloud/Pi3 profile stays disabled. Never accept paths, units or
shell commands from a browser. Internal state/config endpoints use relay auth.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import math
import threading
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from wifiguard_db.models import Device, EventLog, TenantConfig

from ..deps import Scope, add_log, get_db, get_scope, scoped_get
from ..errors import ApiError, forbidden

router = APIRouter(prefix="/local-edge", tags=["local-edge"])
internal_router = APIRouter(prefix="/internal/edge", include_in_schema=False)
_command_lock = threading.RLock()


def fresh(value, seconds):
    stamp = value.get("updated_at_unix") if isinstance(value, dict) else None
    return isinstance(stamp, (int, float)) and math.isfinite(stamp) and 0 <= time.time() - stamp < seconds


class CommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID
    action: Literal["start", "stop", "recalibrate", "confirm-empty-room"]
    run_id: UUID | None = None
    empty_room_confirmed: bool = False


class BindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID


class CalibrationReport(BaseModel):
    run_id: UUID
    phase: Literal["waiting_empty_room", "leaving", "waiting_csi", "measuring", "computing", "saving", "done", "error", "cancelled", "disabled"]
    phase_elapsed_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    leave_wait_seconds: float = Field(default=30, ge=0, allow_inf_nan=False)
    measurement_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    presence_mv_threshold: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    wander_baseline: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    error: str | None = Field(default=None, max_length=1000)


class RuntimeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID
    tenant_id: str = Field(max_length=100)
    calibration: CalibrationReport | None = None


def enabled():
    token = os.getenv("LOCAL_EDGE_TOKEN", "")
    return os.getenv("PI5_CONTROL_ENABLED") == "1" and len(token.encode()) >= 32 and "REPLACE" not in token


def directory():
    return Path(os.getenv("PI5_STATE_DIR", "/var/lib/wifiguard-pi5"))


def read_state(name):
    path = directory() / name
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        def safe(item):
            if isinstance(item, float) and not math.isfinite(item):
                return None
            if isinstance(item, dict):
                return {key: safe(val) for key, val in item.items()}
            if isinstance(item, list):
                return [safe(val) for val in item]
            return item
        return safe(value) if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def atomic_state(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False)
        tmp.chmod(0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def binding():
    value = read_state("device-binding.json")
    if value is None:
        value = {"device_id": os.getenv("LOCAL_DEVICE_ID"), "tenant_id": os.getenv("LOCAL_TENANT_ID")}
    try:
        UUID(value["device_id"])
        if not value["tenant_id"] or "REPLACE" in value["tenant_id"]:
            return None
        return value
    except (ValueError, TypeError, KeyError):
        return None


def tenant(dev):
    return str(dev.facility_id) if dev.facility_id else f"home-{dev.owner_user_id}"


def require_enabled():
    if not enabled():
        raise ApiError(404, "LOCAL_CONTROL_DISABLED", "Pi5 로컬 운영 제어가 설치되지 않았습니다.")


def local_device(db, scope, device_id=None):
    bound = binding()
    target = device_id or (UUID(bound["device_id"]) if bound else None)
    if target is None:
        return None
    dev = scoped_get(db, Device, target, scope, what="장치")
    if not bound or str(dev.id) != bound["device_id"] or tenant(dev) != bound["tenant_id"]:
        raise ApiError(409, "LOCAL_DEVICE_NOT_BOUND", "이 장치는 Pi5 수신기에 연결되어 있지 않습니다.")
    return dev


@router.get("/status")
def status(device_id: UUID | None = None, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    if not enabled():
        return {"enabled": False}
    dev = local_device(db, scope, device_id)
    agent = read_state("control-agent.json") or {}
    agent_fresh = fresh(agent, 10)
    runtime = read_state("status.json")
    runtime_current = bool(dev and runtime and runtime.get("device_id") == str(dev.id)
                           and fresh(runtime, 3))
    calibration = read_state("startup-calibration.json") if runtime_current else None
    if calibration:
        live_calibration = runtime.get("pi5_services", {}).get("startup_calibration", {})
        if calibration.get("run_id") != live_calibration.get("run_id"):
            calibration = None
        elif calibration.get("phase") == live_calibration.get("phase"):
            # The transition file is written once per phase. Elapsed time is
            # updated each runtime report, not frozen at the transition's zero.
            calibration = {**calibration, "phase_elapsed_seconds": live_calibration.get(
                "phase_elapsed_seconds", calibration.get("phase_elapsed_seconds"))}
    resources = read_state("stack-status.json")
    if resources:
        resources = {**resources, "fresh": fresh(resources, 5)}
    return {"enabled": True, "device_id": str(dev.id) if dev else None,
            "agent_available": agent_fresh, "agent": agent,
            "runtime_current": runtime_current, "runtime": runtime if runtime_current else None,
            "calibration": calibration,
            "resources": resources, "last_command": read_state("last-command.json"),
            "desired_revision": config_payload(db, dev)["revision"] if dev else None,
            "simulation": read_state("pc-simulation-status.json") if os.getenv("PC_SIMULATION_MODE") == "1" else None,
            "capabilities": {"transport": "spi", "firmware_agc": False,
                             "firmware_agc_reason": "SPI WGSP에는 UART train 명령이 없습니다.",
                             "absence_grace_seconds": 10, "mlops": False}}


@router.post("/bind")
def bind(body: BindingIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    require_enabled()
    if not scope.can_edit_config():
        raise forbidden()
    dev = scoped_get(db, Device, body.device_id, scope, what="장치")
    if dev.connection != "SPI":
        raise ApiError(422, "SPI_DEVICE_REQUIRED", "SPI로 등록한 장치를 선택하세요.")
    old = binding()
    if old and old["tenant_id"] != tenant(dev):
        raise forbidden("다른 계정/시설에 연결된 Pi5를 가져올 수 없습니다.")
    agent = read_state("control-agent.json") or {}
    if not fresh(agent, 10) or agent.get("edge_state") not in ("inactive", "failed"):
        raise ApiError(409, "STOP_EDGE_FIRST", "제어 서비스 연결을 확인하고 수신을 먼저 중지하세요.")
    with _command_lock:
        if any((directory() / "commands").glob("*.json")):
            raise ApiError(409, "COMMAND_PENDING", "진행 중인 제어 명령이 끝난 뒤 장치를 연결하세요.")
        atomic_state(directory() / "device-binding.json", {"device_id": str(dev.id), "tenant_id": tenant(dev)})
    add_log(db, scope, "INFO", f"Pi5 SPI 수신기 연결: {dev.name}", device_id=dev.id)
    db.commit()
    return {"bound": True, "device_id": str(dev.id)}


@router.post("/commands", status_code=202)
def command(body: CommandIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    require_enabled()
    if not scope.can_edit_config():
        raise forbidden()
    dev = local_device(db, scope, body.device_id)
    agent = read_state("control-agent.json") or {}
    if not fresh(agent, 10):
        raise ApiError(503, "CONTROL_AGENT_OFFLINE", "Pi5 제어 서비스가 응답하지 않습니다.")
    if body.action == "confirm-empty-room":
        state = read_state("startup-calibration.json") or {}
        runtime = read_state("status.json") or {}
        if (not body.empty_room_confirmed or str(body.run_id) != state.get("run_id")
                or runtime.get("device_id") != str(dev.id) or not runtime.get("running") or not fresh(runtime, 3)
                or state.get("run_id") != runtime.get("pi5_services", {}).get("startup_calibration", {}).get("run_id")
                or state.get("phase") != "waiting_empty_room"):
            raise ApiError(409, "CALIBRATION_CONFIRMATION_REQUIRED", "현재 보정 실행의 빈방 상태를 명시적으로 확인하세요.")
    pending = directory() / "commands"
    pending.mkdir(parents=True, exist_ok=True)
    with _command_lock:
        local_device(db, scope, body.device_id)  # binding cannot change under us
        if sum(1 for _ in pending.glob("*.json")) >= 16:
            raise ApiError(429, "CONTROL_QUEUE_FULL", "진행 중인 명령을 기다려주세요.")
        identity = uuid4()
        atomic_state(pending / f"{identity}.json", {**body.model_dump(mode="json"),
                     "id": str(identity), "tenant_id": tenant(dev), "created_at_unix": time.time()})
    add_log(db, scope, "INFO", f"Pi5 명령 접수: {body.action}", device_id=dev.id)
    db.commit()
    return {"id": str(identity), "state": "queued", "action": body.action}


def internal_device(request, db, device_id):
    from ..local_edge import authenticate_local
    authenticate_local(request)
    dev = db.get(Device, device_id)
    bound = binding()
    if not dev or not bound or str(dev.id) != bound["device_id"] or tenant(dev) != bound["tenant_id"]:
        raise ApiError(403, "LOCAL_IDENTITY_MISMATCH", "Pi5 장치 연결이 일치하지 않습니다.")
    return dev


def config_payload(db, dev):
    cond = TenantConfig.facility_id == dev.facility_id if dev.facility_id else TenantConfig.owner_user_id == dev.owner_user_id
    cfg = db.execute(select(TenantConfig).where(cond)).scalar_one_or_none()
    values = {key: getattr(cfg, key) for key in ("presence_mv_threshold", "wander_ratio_threshold", "presence_timeout_s", "threshold", "cooldown_seconds")} if cfg and cfg.updated_by else None
    payload = {"bound": True, "device_id": str(dev.id), "tenant_id": tenant(dev), "settings": values}
    payload["revision"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


@internal_router.get("/config")
def runtime_config(request: Request, db: Session = Depends(get_db)):
    require_enabled()
    bound = binding()
    if not bound:
        from ..local_edge import authenticate_local
        authenticate_local(request)
        return {"bound": False}
    dev = internal_device(request, db, UUID(bound["device_id"]))
    payload = config_payload(db, dev)
    values = payload["settings"]
    service = request.app.state.ingest
    if values:
        service.fall_state.set_cooldown(dev.id, values["cooldown_seconds"])
    return payload


@internal_router.post("/runtime")
def runtime_report(body: RuntimeReport, request: Request, db: Session = Depends(get_db)):
    require_enabled()
    dev = internal_device(request, db, body.device_id)
    if body.tenant_id != tenant(dev):
        raise forbidden()
    value = body.calibration
    if value and value.phase != "disabled":
        if value.phase == "done" and (value.presence_mv_threshold is None or value.wander_baseline is None):
            raise ApiError(422, "BASELINE_REQUIRED", "완료된 보정에는 실제 계산 결과가 필요합니다.")
        stages = {"waiting_empty_room": "IDLE", "leaving": "LEAVING", "waiting_csi": "WAITING_ACK",
                  "measuring": "MEASURING", "computing": "MEASURING", "saving": "MEASURING",
                  "done": "DONE", "error": "ERROR", "cancelled": "ERROR"}
        changed = dev.calibration_run_id != str(value.run_id) or dev.calibration_stage != stages[value.phase]
        if not changed and value.phase in ("done", "error", "cancelled"):
            return {"accepted": True}
        dev.calibration_run_id = str(value.run_id)
        dev.calibration_stage = stages[value.phase]
        dev.calibrating = value.phase not in ("done", "error", "cancelled")
        duration = value.leave_wait_seconds if value.phase == "leaving" else value.measurement_seconds
        dev.calibration_progress = (
            1 if value.phase in ("done", "computing", "saving") else
            min(1, value.phase_elapsed_seconds / max(1, duration)) if value.phase in ("leaving", "measuring") else 0
        )
        if value.phase == "done":
            dev.presence_mv_threshold, dev.wander_baseline = value.presence_mv_threshold, value.wander_baseline
            if changed:
                cond = TenantConfig.facility_id == dev.facility_id if dev.facility_id else TenantConfig.owner_user_id == dev.owner_user_id
                cfg = db.execute(select(TenantConfig).where(cond)).scalar_one_or_none()
                if cfg is None:
                    cfg = TenantConfig(facility_id=dev.facility_id, owner_user_id=dev.owner_user_id)
                    db.add(cfg)
                # Display the real MV baseline, not the former demo/default.
                cfg.presence_mv_threshold = value.presence_mv_threshold
        if changed:
            db.add(EventLog(device_id=dev.id, facility_id=dev.facility_id, owner_user_id=dev.owner_user_id,
                            level="ERROR" if value.phase in ("error", "cancelled") else "INFO",
                            msg=f"{'PC 모의 RAW 기준값 보정' if os.getenv('PC_SIMULATION_MODE') == '1' else 'Pi5 실제 빈방 보정'}: {value.phase} · {value.error or str(value.run_id)}"))
        db.commit()
    return {"accepted": True}
