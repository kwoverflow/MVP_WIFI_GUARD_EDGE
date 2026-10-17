from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wifiguard_contracts.api import ErrorOut, EventLogIn, EventLogOut, EventLogPage, LogLevel
from wifiguard_db.models import Device, EventLog, Resident

from ..deps import Scope, add_log, assert_in_scope, get_db, get_scope, scope_filter

router = APIRouter(prefix="/event-logs", tags=["event-logs"])
_ERR = {401: {"model": ErrorOut}, 400: {"model": ErrorOut}}


@router.get("", response_model=EventLogPage, responses=_ERR)
def list_logs(
    level: LogLevel | None = None,
    q: str | None = Query(default=None, max_length=200),
    resident_id: uuid.UUID | None = Query(default=None, alias="residentId"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
) -> EventLogPage:
    where = [scope_filter(EventLog, scope)]
    if level:
        where.append(EventLog.level == level)
    if q:
        where.append(EventLog.msg.ilike(f"%{q.strip()}%"))
    if resident_id:
        where.append(EventLog.resident_id == resident_id)
    if from_:
        where.append(EventLog.ts >= from_)
    if to:
        where.append(EventLog.ts <= to)
    total = db.execute(select(func.count()).select_from(EventLog).where(*where)).scalar_one()
    rows = db.execute(select(EventLog).where(*where).order_by(EventLog.ts.desc(), EventLog.id.desc()).limit(limit).offset(offset)).scalars()
    return EventLogPage(items=[EventLogOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=EventLogOut, status_code=status.HTTP_201_CREATED, responses=_ERR)
def post_log(body: EventLogIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> EventLog:
    """클라이언트 발생 로그(INFO/WARN/ERROR). FALL 은 서버 전용이라 스키마에서 거부된다."""
    if body.resident_id:
        assert_in_scope(db, Resident, [body.resident_id], scope, field="residentId")
    if body.device_id:
        assert_in_scope(db, Device, [body.device_id], scope, field="deviceId")
    log = add_log(db, scope, body.level, body.msg.strip(), resident_id=body.resident_id, device_id=body.device_id)
    db.commit()
    db.refresh(log)
    return log
