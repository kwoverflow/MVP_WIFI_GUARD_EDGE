"""공통 의존성 — DB 세션, 인증(Scope), 역할 게이트, 테넌시 스코핑.

모든 라우터는 여기의 `Scope` 로 시작한다. 스코프 밖 리소스는 404 로 답해 존재 여부를 노출하지 않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from wifiguard_db.engine import SessionLocal
from wifiguard_db.models import EventLog, User

from .auth.jwt import AccessTokenError, decode_access_token
from .errors import ApiError, forbidden, not_found, unauthorized

T = TypeVar("T")

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@dataclass(frozen=True)
class Scope:
    user_id: uuid.UUID
    service: str  # HOME | FACILITY
    role: str  # ROOT | MEMBER | USER
    facility_id: uuid.UUID | None
    user: User

    @property
    def is_facility(self) -> bool:
        return self.service == "FACILITY"

    def can_edit_config(self) -> bool:
        return self.role in ("ROOT", "USER")


def get_scope(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Scope:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized("UNAUTHORIZED", "인증 토큰이 없습니다.")
    try:
        payload = decode_access_token(credentials.credentials)
    except AccessTokenError as exc:
        raise unauthorized(str(exc), "토큰이 유효하지 않거나 만료되었습니다.") from exc
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise unauthorized("TOKEN_INVALID", "토큰이 유효하지 않습니다.") from exc
    user = db.get(User, user_id)
    if user is None:  # 제거된 멤버 — access 잔여 시간과 무관하게 즉시 차단
        raise unauthorized("USER_NOT_FOUND", "계정을 찾을 수 없습니다.")
    # 역할·소속은 토큰이 아니라 DB 행을 진실로 삼는다.
    return Scope(user_id=user.id, service=user.service, role=user.role, facility_id=user.facility_id, user=user)


def require_roles(*roles: str) -> Callable[..., Scope]:
    def _dep(scope: Scope = Depends(get_scope)) -> Scope:
        if scope.role not in roles:
            raise forbidden()
        return scope

    return _dep


def require_facility(scope: Scope = Depends(get_scope)) -> Scope:
    if not scope.is_facility or scope.facility_id is None:
        raise forbidden("시설 계정만 사용할 수 있습니다.")
    return scope


# ── 테넌시 스코핑 ──────────────────────────────────────────────────
def scope_filter(model: Any, scope: Scope) -> ColumnElement[bool]:
    """모든 목록/단건 조회의 첫 조건."""
    if scope.is_facility:
        return model.facility_id == scope.facility_id
    return model.owner_user_id == scope.user_id


def stamp_scope(obj: Any, scope: Scope) -> None:
    """생성 시 XOR 컬럼을 채운다. 클라이언트가 보낸 facilityId/ownerUserId 는 무시한다."""
    if scope.is_facility:
        obj.facility_id = scope.facility_id
        obj.owner_user_id = None
    else:
        obj.facility_id = None
        obj.owner_user_id = scope.user_id


def scoped_get(db: Session, model: type[T], obj_id: uuid.UUID, scope: Scope, *, what: str = "리소스") -> T:
    obj = db.execute(select(model).where(model.id == obj_id, scope_filter(model, scope))).scalar_one_or_none()
    if obj is None:
        raise not_found(what)
    return obj


def assert_in_scope(db: Session, model: Any, ids: list[uuid.UUID], scope: Scope, *, field: str) -> None:
    """참조 무결성 — 참조된 id 들이 전부 같은 스코프에 존재해야 한다 (아니면 400 SCOPE_MISMATCH)."""
    if not ids:
        return
    found = set(db.execute(select(model.id).where(model.id.in_(ids), scope_filter(model, scope))).scalars())
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise ApiError(400, "SCOPE_MISMATCH", "다른 스코프의 리소스를 참조할 수 없습니다.", {field: missing})


def add_log(db: Session, scope: Scope, level: str, msg: str, **refs: Any) -> EventLog:
    """테넌트 스코프의 이벤트 로그(감사 이력) 1행."""
    log = EventLog(level=level, msg=msg, actor_user_id=scope.user_id, **refs)
    stamp_scope(log, scope)
    db.add(log)
    return log


def client_meta(request: Request) -> tuple[str | None, str | None]:
    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    return ua, ip
