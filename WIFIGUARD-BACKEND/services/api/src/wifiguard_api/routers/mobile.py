"""JWT-authenticated app registration, tests, delivery receipts and private status."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wifiguard_contracts.api import ApiModel
from wifiguard_db.models import MobileInstallation, MobilePushDelivery, FallEvent
from wifiguard_notify.mobile import enabled, test_push_enabled, enqueue, authorized

from ..deps import Scope, get_db, get_scope, scoped_get, add_log
from ..errors import ApiError, not_found

router = APIRouter(prefix="/mobile", tags=["mobile push"])


class InstallationIn(ApiModel):
    installation_id: uuid.UUID
    fcm_token: str = Field(min_length=32, max_length=4096, pattern=r"^[A-Za-z0-9:_-]+$")
    label: str = Field(default="Android", min_length=1, max_length=80)
    platform: Literal["android"] = "android"


class InstallationOut(ApiModel):
    id: uuid.UUID
    installation_id: uuid.UUID
    label: str
    enabled: bool


class TestIn(ApiModel):
    confirmed: Literal[True]


class ReceiptIn(ApiModel):
    installation_id: uuid.UUID
    displayed: bool = False


def require_enabled():
    if not test_push_enabled():
        raise ApiError(503, "MOBILE_PUSH_DISABLED", "앱 푸시가 아직 설정되지 않았거나 PC 모의시험에서 차단되어 있습니다.")


def own_installation(db, scope, installation_id, *, lock=False):
    statement = select(MobileInstallation).where(MobileInstallation.installation_id == installation_id,
                                                 MobileInstallation.user_id == scope.user_id)
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise not_found("앱 기기")
    return row


@router.get("/config")
def config(scope: Scope = Depends(get_scope)):
    return {"pushEnabled": test_push_enabled(), "provider": "fcm", "androidPackage": "com.wifiguard.mobile",
            "automaticFallPushEnabled": enabled(), "testOnly": os.getenv("PC_SIMULATION_MODE") == "1",
            "firebaseProjectId": os.getenv("FCM_PROJECT_ID", "") if test_push_enabled() else "",
            "requiresFirebaseClientConfig": True}


@router.get("/installations", response_model=list[InstallationOut])
def installations(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    return list(db.execute(select(MobileInstallation).where(MobileInstallation.user_id == scope.user_id)
                           .order_by(MobileInstallation.created_at)).scalars())


@router.put("/installations", response_model=InstallationOut)
def register(body: InstallationIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    require_enabled()
    digest = hashlib.sha256(body.fcm_token.encode()).hexdigest()
    row = db.execute(select(MobileInstallation).where(MobileInstallation.installation_id == body.installation_id)).scalar_one_or_none()
    token_row = db.execute(select(MobileInstallation).where(MobileInstallation.token_hash == digest)).scalar_one_or_none()
    if (row and row.user_id != scope.user_id) or (token_row and token_row is not row):
        raise ApiError(409, "INSTALLATION_CONFLICT", "기기 등록을 초기화한 뒤 다시 시도하세요.")
    if row is None:
        row = MobileInstallation(installation_id=body.installation_id, user_id=scope.user_id,
                                 facility_id=scope.facility_id, fcm_token=body.fcm_token, token_hash=digest, label=body.label.strip())
        db.add(row)
    else:
        row.fcm_token, row.token_hash, row.label = body.fcm_token, digest, body.label.strip()
        row.enabled, row.facility_id = True, scope.facility_id
    try:
        db.flush()
        add_log(db, scope, "INFO", "모바일 알림 기기 등록/갱신")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "INSTALLATION_CONFLICT", "기기 등록을 초기화한 뒤 다시 시도하세요.") from exc
    return row


@router.delete("/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(installation_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    row = own_installation(db, scope, installation_id)
    row.enabled = False
    row.fcm_token = "revoked"  # Erase usable addressing information on logout.
    row.token_hash = f"revoked:{row.id}"
    for delivery in db.execute(select(MobilePushDelivery).where(MobilePushDelivery.installation_id == row.id,
                                                               MobilePushDelivery.state == "PENDING")).scalars():
        delivery.state, delivery.last_error = "REVOKED", "RECIPIENT_REVOKED"
    add_log(db, scope, "INFO", "모바일 알림 기기 등록 해제")
    db.commit()


@router.post("/installations/{installation_id}/test", status_code=status.HTTP_202_ACCEPTED)
def test_push(installation_id: uuid.UUID, body: TestIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    require_enabled()
    # Serialize same-device requests so concurrent tests cannot bypass the quota.
    row = own_installation(db, scope, installation_id, lock=True)
    if not authorized(row, scope.user):
        raise ApiError(409, "INSTALLATION_DISABLED", "기기를 다시 등록하세요.")
    now = datetime.now(UTC)
    count = db.execute(select(func.count()).select_from(MobilePushDelivery).where(
        MobilePushDelivery.installation_id == row.id, MobilePushDelivery.kind == "TEST",
        MobilePushDelivery.created_at >= now - timedelta(minutes=1))).scalar_one()
    if count >= 3:
        raise ApiError(429, "PUSH_TEST_RATE_LIMIT", "시험 알림은 기기당 1분에 3회까지 가능합니다.")
    delivery = enqueue(db, row.id, "TEST")
    db.flush()
    db.commit()
    return {"deliveryId": str(delivery.id), "state": "PENDING", "phoneReceived": False}


@router.get("/deliveries")
def deliveries(limit: int = Query(default=30, ge=1, le=100), scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    rows = db.execute(select(MobilePushDelivery, MobileInstallation).join(MobileInstallation)
                      .where(MobileInstallation.user_id == scope.user_id)
                      .order_by(MobilePushDelivery.created_at.desc()).limit(limit))
    return [{"deliveryId": str(d.id), "installationId": str(i.installation_id), "kind": d.kind,
             "fallEventId": str(d.fall_event_id) if d.fall_event_id else None, "state": d.state,
             "attempts": d.attempts, "createdAt": d.created_at, "acceptedAt": d.accepted_at,
             "receivedAt": d.received_at, "displayedAt": d.displayed_at, "lastError": d.last_error}
            for d, i in rows]


@router.post("/deliveries/{delivery_id}/receipt")
def receipt(delivery_id: uuid.UUID, body: ReceiptIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    installation = own_installation(db, scope, body.installation_id)
    delivery = db.execute(select(MobilePushDelivery).where(MobilePushDelivery.id == delivery_id,
                                                           MobilePushDelivery.installation_id == installation.id)).scalar_one_or_none()
    if delivery is None or not authorized(installation, scope.user):
        raise not_found("알림")
    if delivery.fall_event_id:
        scoped_get(db, FallEvent, delivery.fall_event_id, scope, what="낙상 이벤트")
    delivery.received_at = delivery.received_at or datetime.now(UTC)
    if body.displayed:
        delivery.displayed_at = delivery.displayed_at or datetime.now(UTC)
    db.commit()
    # Receipt is telemetry only, NOT an operator response to the fall.
    return {"ok": True, "operatorAcknowledged": False}
