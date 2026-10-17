from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from wifiguard_contracts.api import ErrorOut, TenantConfigIn, TenantConfigOut
from wifiguard_db.models import TenantConfig

from ..deps import Scope, add_log, get_db, get_scope, stamp_scope
from ..errors import forbidden

router = APIRouter(prefix="/config", tags=["config"])
_ERR = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}}


def get_or_create_config(db: Session, scope: Scope) -> TenantConfig:
    cond = (TenantConfig.facility_id == scope.facility_id) if scope.is_facility else (TenantConfig.owner_user_id == scope.user_id)
    cfg = db.execute(select(TenantConfig).where(cond)).scalar_one_or_none()
    if cfg is None:
        cfg = TenantConfig()
        stamp_scope(cfg, scope)
        db.add(cfg)
        db.flush()
    return cfg


@router.get("", response_model=TenantConfigOut, responses=_ERR)
def get_config(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> TenantConfig:
    cfg = get_or_create_config(db, scope)
    db.commit()
    return cfg


@router.put("", response_model=TenantConfigOut, responses=_ERR)
def put_config(body: TenantConfigIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> TenantConfig:
    """ROOT(시설) 또는 HOME 사용자만. MEMBER 는 403 (기능명세 v1.4 §2.2)."""
    if not scope.can_edit_config():
        raise forbidden("알고리즘 설정은 시설 등록자(ROOT)만 변경할 수 있습니다.")
    cfg = get_or_create_config(db, scope)
    for key, value in body.model_dump().items():
        setattr(cfg, key, value)
    cfg.updated_by = scope.user_id
    add_log(db, scope, "INFO", "탐지 설정 저장됨 · Pi5 실행 적용은 런타임 확인 필요")
    db.commit()
    db.refresh(cfg)
    return cfg
