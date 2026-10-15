from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from wifiguard_contracts.api import ErrorOut, FacilityOut, FacilityPatch, MemberOut
from wifiguard_db.models import Facility, User

from ..auth.service import generate_invite_code
from ..deps import Scope, add_log, get_db, require_facility, require_roles
from ..errors import ApiError, not_found

router = APIRouter(prefix="/facilities", tags=["facilities"])
_ERR = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}}


def _facility(db: Session, scope: Scope) -> Facility:
    fac = db.get(Facility, scope.facility_id) if scope.facility_id else None
    if fac is None:
        raise not_found("시설")
    return fac


@router.get("/me", response_model=FacilityOut, responses=_ERR)
def get_my_facility(scope: Scope = Depends(require_facility), db: Session = Depends(get_db)) -> Facility:
    return _facility(db, scope)


@router.patch("/me", response_model=FacilityOut, responses=_ERR)
def patch_my_facility(body: FacilityPatch, scope: Scope = Depends(require_roles("ROOT")), db: Session = Depends(get_db)) -> Facility:
    fac = _facility(db, scope)
    fac.name = body.name.strip()
    add_log(db, scope, "INFO", f"시설명 변경: {fac.name}")
    db.commit()
    return fac


@router.get("/me/members", response_model=list[MemberOut], responses=_ERR)
def list_members(scope: Scope = Depends(require_roles("ROOT")), db: Session = Depends(get_db)) -> list[User]:
    return list(db.execute(
        select(User).where(User.facility_id == scope.facility_id).order_by(User.created_at)
    ).scalars())


@router.delete("/me/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT,
               responses={**_ERR, 400: {"model": ErrorOut}})
def remove_member(user_id: uuid.UUID, scope: Scope = Depends(require_roles("ROOT")), db: Session = Depends(get_db)) -> Response:
    fac = _facility(db, scope)
    target = db.execute(select(User).where(User.id == user_id, User.facility_id == fac.id)).scalar_one_or_none()
    if target is None:
        raise not_found("멤버")
    if target.id == fac.root_user_id or target.role == "ROOT":
        raise ApiError(400, "CANNOT_REMOVE_ROOT", "시설 등록자(ROOT)는 제거할 수 없습니다.")
    add_log(db, scope, "WARN", f"멤버 제거: {target.name} ({target.email})")
    db.delete(target)  # refresh_tokens 는 CASCADE
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/invite-code", response_model=FacilityOut, responses=_ERR)
def regenerate_invite_code(scope: Scope = Depends(require_roles("ROOT")), db: Session = Depends(get_db)) -> Facility:
    fac = _facility(db, scope)
    fac.invite_code = generate_invite_code(db, fac.name)
    add_log(db, scope, "INFO", "초대 코드 재발급")
    db.commit()
    return fac
