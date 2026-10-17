from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from wifiguard_contracts.api import AccountPatch, ErrorOut, PasswordChangeIn, TokenPair, UserOut

from ..auth import service as auth_service
from ..deps import Scope, add_log, client_meta, get_db, get_scope
from ..errors import ApiError

router = APIRouter(prefix="/account", tags=["account"])
_ERR = {401: {"model": ErrorOut}}


@router.patch("", response_model=UserOut, responses={**_ERR, 409: {"model": ErrorOut}})
def patch_account(body: AccountPatch, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    user = scope.user
    if body.email is not None:
        new_email = auth_service.normalize_email(body.email)
        other = auth_service.find_user_by_email(db, new_email)
        if other is not None and other.id != user.id:
            raise ApiError(409, "EMAIL_TAKEN", "이미 사용 중인 이메일입니다.")
        user.email = new_email
    if body.name is not None:
        user.name = body.name.strip()
    add_log(db, scope, "INFO", "계정 정보 업데이트됨")
    db.commit()
    return user


@router.post("/password", response_model=TokenPair, responses={**_ERR, 400: {"model": ErrorOut}})
def change_password(body: PasswordChangeIn, request: Request, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> TokenPair:
    """비밀번호 변경 → 기존 refresh 전부 폐기 → 새 토큰 쌍 발급."""
    auth_service.change_password(db, scope.user, body.current_password, body.new_password)
    ua, ip = client_meta(request)
    pair = auth_service.issue_token_pair(db, scope.user, user_agent=ua, ip=ip)
    db.commit()
    return TokenPair(access_token=pair.access_token, refresh_token=pair.refresh_token,
                     expires_in=pair.expires_in, user=UserOut.model_validate(pair.user))
