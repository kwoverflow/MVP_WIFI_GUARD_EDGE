from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wifiguard_contracts.api import ErrorOut, FallOut, FallPage, FallResponse, FallResponseIn, FallSimulateIn
from wifiguard_db.models import FallEvent, Resident

from ..config import settings
from ..deps import Scope, add_log, get_db, get_scope, scope_filter, scoped_get, stamp_scope
from ..errors import not_found

router = APIRouter(prefix="/falls", tags=["falls"])
_ERR = {401: {"model": ErrorOut}, 404: {"model": ErrorOut}}

RESPONSE_LABEL = {"PENDING": "대기중", "ACKNOWLEDGED": "확인함", "DISPATCHED": "출동중", "FALSE_ALARM": "오탐지"}


@router.get("", response_model=FallPage, responses=_ERR)
def list_falls(
    response: FallResponse | None = None,
    resident_id: uuid.UUID | None = Query(default=None, alias="residentId"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
) -> FallPage:
    where = [scope_filter(FallEvent, scope)]
    if response:
        where.append(FallEvent.response == response)
    if resident_id:
        where.append(FallEvent.resident_id == resident_id)
    if from_:
        where.append(FallEvent.occurred_at >= from_)
    if to:
        where.append(FallEvent.occurred_at <= to)
    total = db.execute(select(func.count()).select_from(FallEvent).where(*where)).scalar_one()
    rows = db.execute(
        select(FallEvent).where(*where).order_by(FallEvent.occurred_at.desc()).limit(limit).offset(offset)
    ).scalars()
    return FallPage(items=[FallOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{fall_id}", response_model=FallOut, responses=_ERR)
def get_fall(fall_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> FallEvent:
    return scoped_get(db, FallEvent, fall_id, scope, what="낙상 이벤트")


@router.patch("/{fall_id}/response", response_model=FallOut, responses=_ERR)
def respond(fall_id: uuid.UUID, body: FallResponseIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> FallEvent:
    fall = scoped_get(db, FallEvent, fall_id, scope, what="낙상 이벤트")
    fall.response = body.response
    fall.responded_by = scope.user_id
    fall.responded_at = datetime.now(UTC)
    add_log(db, scope, "INFO", f"알람 응답: {body.response} ({RESPONSE_LABEL[body.response]}) — {fall.resident_name}",
            fall_event_id=fall.id, resident_id=fall.resident_id)
    db.commit()
    return fall


@router.post("/simulate", response_model=FallOut, status_code=status.HTTP_201_CREATED, responses=_ERR)
def simulate(body: FallSimulateIn | None = None, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> FallEvent:
    """데모·알람 경로 검증용. ALLOW_FALL_SIMULATE=0 이면 404 (존재를 숨긴다)."""
    if not settings.allow_fall_simulate:
        raise not_found("리소스")
    resident: Resident | None
    if body and body.resident_id:
        resident = scoped_get(db, Resident, body.resident_id, scope, what="거주자")
    else:
        resident = db.execute(
            select(Resident).where(scope_filter(Resident, scope)).order_by(Resident.created_at)
        ).scalars().first()
    if resident is not None:
        name, room, rid, did = resident.name, resident.room, resident.id, resident.primary_device_id
    else:  # mock-store simulateFall: 거주자가 없으면 본인
        name, room, rid, did = scope.user.name, "거실", None, None
    fall = FallEvent(
        resident_id=rid, resident_name=name, room=room, device_id=did, occurred_at=datetime.now(UTC),
        confidence=round(0.88 + random.random() * 0.1, 3), duration_s=round(0.9 + random.random() * 0.6, 2),
        source="SIMULATED", response="PENDING",
    )
    stamp_scope(fall, scope)
    db.add(fall)
    db.flush()
    add_log(db, scope, "FALL", f"[시뮬레이션] {name} ({room}) 낙상 감지 · 신뢰도 {fall.confidence:.2f}",
            fall_event_id=fall.id, resident_id=rid, device_id=did)
    db.commit()
    return fall
