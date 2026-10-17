from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from wifiguard_contracts.api import ErrorOut, ResidentIn, ResidentOut, ResidentPatch
from wifiguard_db.models import Device, Resident, ResidentDevice

from ..deps import Scope, add_log, assert_in_scope, get_db, get_scope, scope_filter, scoped_get, stamp_scope

router = APIRouter(prefix="/residents", tags=["residents"])
_ERR = {401: {"model": ErrorOut}, 404: {"model": ErrorOut}, 400: {"model": ErrorOut}}


def to_out(r: Resident) -> ResidentOut:
    """런타임 7필드는 **주 장치**의 최신 상태에서 채운다.

    캐시에 없으면 null 로 남고, 프론트는 그것을 "감지 미동작"으로 표시한다 — "퇴실"과
    구별되어야 하므로 기본값을 채워 넣으면 안 된다.

    거주자가 여러 기기에 매핑돼도 주 장치 하나만 본다. 여러 기기의 상태를 합치는 규칙
    (어느 쪽 재실을 믿을 것인가)이 아직 정의되지 않았고, 임의로 정하면 나중에 바꾸기 어렵다.
    """
    live = _live_state(r.primary_device_id)
    presence = (live.presence or {}) if live else {}
    fall = (live.fall or {}) if live else {}
    state = presence.get("state")
    return ResidentOut(
        id=r.id, name=r.name, room=r.room, age=r.age, caregiver=r.caregiver,
        device_id=r.primary_device_id, device_ids=r.device_ids, threshold_override=r.threshold_override,
        facility_id=r.facility_id, owner_user_id=r.owner_user_id, created_at=r.created_at, updated_at=r.updated_at,
        # 재실 축 — 와이어는 소문자(present/absent), REST 는 대문자다
        presence=state.upper() if state else None,
        mv=presence.get("mv_current"),
        wander=presence.get("wander_current"),
        last_activity_at=_epoch_to_dt(presence.get("last_activity_at")),
        online=live.online(_offline_after_s()) if live else None,
        # 낙상 축 — 모델서버가 붙기 전까지 항상 null (M5)
        state=fall.get("detect_state"),
        confidence=fall.get("proba_fall"),
    )


def _live_state(device_id: uuid.UUID | None):
    """`LiveCache` 조회. 인제스트가 꺼져 있으면 None — CRUD 는 그대로 동작해야 한다."""
    if device_id is None:
        return None
    try:
        from wifiguard_ingest.service import get_service
    except ImportError:
        return None
    service = get_service()
    return service.cache.get(device_id) if service else None


def _offline_after_s() -> float:
    """유예 임계값의 출처는 **LiveCache** 다 — 타임스탬프를 들고 있는 쪽이 소유해야 한다.

    settings 에도 같은 값이 있지만 그쪽은 캐시를 만들 때 쓰는 초기값이고, 런타임에 바꾸면
    두 값이 갈라진다. 읽을 때는 항상 캐시를 본다.
    """
    try:
        from wifiguard_ingest.service import get_service
    except ImportError:
        return 15.0
    service = get_service()
    return service.cache.offline_after_s if service else 15.0


def _epoch_to_dt(value: float | None) -> datetime | None:
    """`last_activity_at` 은 와이어에서 unix epoch 초(float)이고 REST 는 datetime 이다."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def normalize_mapping(device_id: uuid.UUID | None, device_ids: list[uuid.UUID] | None) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
    """residents.tsx:184-192 와 같은 규칙: ids = deviceIds ?? [deviceId]; primary = deviceId if in ids else ids[0].

    deviceIds 가 명시적 빈 목록이면 매핑 없음(primary None). None 일 때만 deviceId 로 대체한다.
    """
    source = device_ids if device_ids is not None else ([device_id] if device_id else [])
    ids: list[uuid.UUID] = []
    for d in source:
        if d not in ids:
            ids.append(d)
    primary = device_id if device_id in ids else (ids[0] if ids else None)
    return primary, ids


def _replace_links(db: Session, r: Resident, primary: uuid.UUID | None, ids: list[uuid.UUID]) -> None:
    r.device_links.clear()
    db.flush()
    for d in ids:
        r.device_links.append(ResidentDevice(resident_id=r.id, device_id=d, is_primary=(d == primary)))


@router.get("", response_model=list[ResidentOut], responses=_ERR)
def list_residents(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> list[ResidentOut]:
    rows = db.execute(select(Resident).where(scope_filter(Resident, scope)).order_by(Resident.created_at)).scalars()
    return [to_out(r) for r in rows]


@router.post("", response_model=ResidentOut, status_code=status.HTTP_201_CREATED, responses=_ERR)
def create_resident(body: ResidentIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> ResidentOut:
    primary, ids = normalize_mapping(body.device_id, body.device_ids)
    assert_in_scope(db, Device, ids, scope, field="deviceIds")
    r = Resident(name=body.name.strip(), room=body.room.strip(), age=body.age, caregiver=body.caregiver,
                 threshold_override=body.threshold_override)
    stamp_scope(r, scope)
    db.add(r)
    db.flush()
    _replace_links(db, r, primary, ids)
    add_log(db, scope, "INFO", f"거주자 등록: {r.name} ({r.room})", resident_id=r.id)
    db.commit()
    db.refresh(r)
    return to_out(r)


@router.get("/{resident_id}", response_model=ResidentOut, responses=_ERR)
def get_resident(resident_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> ResidentOut:
    return to_out(scoped_get(db, Resident, resident_id, scope, what="거주자"))


@router.patch("/{resident_id}", response_model=ResidentOut, responses=_ERR)
def patch_resident(resident_id: uuid.UUID, body: ResidentPatch, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> ResidentOut:
    r = scoped_get(db, Resident, resident_id, scope, what="거주자")
    data = body.model_dump(exclude_unset=True)
    for key in ("name", "room", "age", "caregiver", "threshold_override"):
        if key in data and data[key] is not None:
            setattr(r, key, data[key].strip() if isinstance(data[key], str) else data[key])
    if body.clear_threshold_override:
        r.threshold_override = None
    if "device_id" in data or "device_ids" in data:
        primary_in = body.device_id if "device_id" in data else r.primary_device_id
        ids_in = body.device_ids if "device_ids" in data else r.device_ids
        primary, ids = normalize_mapping(primary_in, ids_in)
        assert_in_scope(db, Device, ids, scope, field="deviceIds")
        _replace_links(db, r, primary, ids)
    add_log(db, scope, "INFO", f"거주자 수정: {r.name} ({r.room})", resident_id=r.id)
    db.commit()
    db.refresh(r)
    return to_out(r)


@router.delete("/{resident_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERR)
def delete_resident(resident_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Response:
    r = scoped_get(db, Resident, resident_id, scope, what="거주자")
    add_log(db, scope, "WARN", f"거주자 삭제: {r.name} ({r.room})")
    db.delete(r)  # recipients.resident_id / fall_events.resident_id / event_logs.resident_id → SET NULL
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
