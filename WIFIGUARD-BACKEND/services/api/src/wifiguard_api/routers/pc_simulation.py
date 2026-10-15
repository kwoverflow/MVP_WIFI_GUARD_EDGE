"""Explicit PC fixture controls; disabled in Pi5/cloud, scoped JWT required.

Changing RAW fixture never overrides a presence decision or model probability.
Alert check injects a clearly labelled confirmed record through the real sink.
"""
import os
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import InferenceResult

from ..deps import Scope, add_log, get_db, get_scope
from ..errors import ApiError, forbidden
from .local_control import atomic_state, directory, local_device, tenant, require_enabled

router = APIRouter(prefix="/local-edge/simulation", tags=["PC simulation"])


def require_pc(scope, db):
    require_enabled()
    if os.getenv("PC_SIMULATION_MODE") != "1":
        raise ApiError(404, "PC_SIMULATION_DISABLED", "PC 모의 입력은 운영 Pi5에서 사용할 수 없습니다.")
    if not scope.can_edit_config():
        raise forbidden()
    dev = local_device(db, scope)
    if not dev:
        raise ApiError(409, "LOCAL_DEVICE_NOT_BOUND", "먼저 모의 수신 장치를 연결하세요.")
    return dev


class ScenarioIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Literal["empty", "motion", "still", "disconnect"]


@router.post("/scenario")
def scenario(body: ScenarioIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    dev = require_pc(scope, db)
    atomic_state(directory() / "simulation-scenario.json", {"scenario": body.scenario, "updated_at_unix": time.time()})
    add_log(db, scope, "INFO", f"[PC 모의 RAW 입력] {body.scenario}", device_id=dev.id)
    db.commit()
    return {"scenario": body.scenario, "simulated_input": True, "forced_model_decision": False}


class AlertTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed_test: Literal[True]


def validate_pc_notification_target(value):
    """Never allow PC fixtures to send push outside the local test inbox."""
    if os.getenv("PC_SIMULATION_MODE") != "1":
        return
    if value.push:
        target = urlsplit(value.ntfy_server or "https://ntfy.sh")
        expected = os.getenv("PC_SIMULATION_PUSH_URL", "")
        if (value.ntfy_server != expected or target.hostname != "127.0.0.1"
                or value.ntfy_topic != "validation-only"):
            raise ApiError(422, "PC_LOCAL_INBOX_ONLY", "PC 모의 시험은 지정한 로컬 알림 수신기만 사용합니다.")


@router.post("/alert-test")
def alert_test(body: AlertTestIn, request: Request, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    dev = require_pc(scope, db)
    # The launcher supplies a loopback SMTP capture, never inherits credentials.
    if os.getenv("SMTP_HOST") != "127.0.0.1":
        raise ApiError(409, "PC_LOCAL_INBOX_ONLY", "로컬 메일 수신기가 필요합니다.")
    service = getattr(request.app.state, "ingest", None)
    if not service or not service.settings.local_enabled:
        raise ApiError(503, "LOCAL_INGEST_OFFLINE", "적재 서비스가 연결되지 않았습니다.")
    from ..local_edge import Envelope, persist_record
    now = datetime.now(UTC)
    record = InferenceResult(device_id=dev.id, tenant_id=tenant(dev), ts=now,
                             inferred_at=now, seq=time.time_ns(), proba_fall=.99,
                             threshold=.5, postprocess="segmentation_b", decision=True,
                             model_version="pc-injected-alert-routing-test-NOT-model-prediction")
    envelope = Envelope(topic=topics.KAFKA_INFERENCE_RESULT, record=record.model_dump(mode="json"))
    result = persist_record(service, envelope, record)
    add_log(db, scope, "WARN", "[PC 시험] 확정 낙상 이벤트 주입: 모델 정확도 검증 아님 · 외부 알림 미발송", device_id=dev.id)
    db.commit()
    return {**result, "injected_event": True, "model_accuracy_verified": False, "external_delivery": False}
