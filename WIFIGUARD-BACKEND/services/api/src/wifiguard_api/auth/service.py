"""인증 서비스 계층 — 가입/로그인/refresh 회전/로그아웃, 초대코드 발급.

refresh 토큰은 JWT 가 아닌 불투명 문자열(`secrets.token_urlsafe(48)`)이며 DB 에는 sha256 만 저장한다.
회전: 사용할 때마다 새 토큰을 같은 family 로 발급하고 이전 토큰에 replaced_by/revoked_at 을 기록한다.
재사용 감지: 이미 회전된(또는 폐기된) 토큰이 다시 오면 family 전체를 폐기한다 (탈취 대응).
"""

from __future__ import annotations

import hashlib
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from wifiguard_db.models import EventLog, Facility, RefreshToken, TenantConfig, User

from ..config import settings
from ..errors import ApiError, unauthorized
from .hashing import hash_password, needs_rehash, verify_password
from .jwt import create_access_token


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


# ── 헬퍼 ────────────────────────────────────────────────────────────
def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(func.lower(User.email) == normalize_email(email))).scalar_one_or_none()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _log(db: Session, user: User, level: str, msg: str) -> None:
    db.add(EventLog(
        level=level, msg=msg, actor_user_id=user.id,
        facility_id=user.facility_id if user.service == "FACILITY" else None,
        owner_user_id=user.id if user.service == "HOME" else None,
    ))


def generate_invite_code(db: Session, facility_name: str) -> str:
    """`[A-Z]{2,3}-\\d{4}` — 시설명의 ASCII 알파벳 앞 2글자를 쓰고, 없으면(한글 등) 랜덤 대문자. UNIQUE 충돌 시 재시도."""
    letters = "".join(ch for ch in facility_name.upper() if ch in string.ascii_uppercase)[:2]
    for _ in range(20):
        prefix = letters if len(letters) == 2 else "".join(secrets.choice(string.ascii_uppercase) for _ in range(2))
        code = f"{prefix}-{secrets.randbelow(9000) + 1000}"
        if db.execute(select(Facility.id).where(Facility.invite_code == code)).first() is None:
            return code
        letters = ""  # 충돌이 나면 랜덤 접두로 전환
    raise ApiError(500, "INVITE_CODE_EXHAUSTED", "초대 코드를 생성하지 못했습니다.")


# ── 가입 · 로그인 ───────────────────────────────────────────────────
def signup(
    db: Session,
    *,
    email: str,
    password: str,
    name: str,
    service: str,
    facility_mode: str | None,
    facility_name: str | None,
    invite_code: str | None,
) -> User:
    email = normalize_email(email)
    if find_user_by_email(db, email) is not None:
        raise ApiError(409, "EMAIL_TAKEN", "이미 사용 중인 이메일입니다.")

    user_id = uuid.uuid4()
    if service == "HOME":
        user = User(id=user_id, email=email, password_hash=hash_password(password), name=name.strip(),
                    service="HOME", role="USER", facility_id=None, onboarded=False)
        db.add(user)
        db.flush()
        db.add(TenantConfig(owner_user_id=user.id))
        _log(db, user, "INFO", f"신규 가입: {user.name} (HOME/USER)")
        return user

    # FACILITY
    if facility_mode == "MEMBER":
        code = (invite_code or "").strip().upper()
        facility = db.execute(select(Facility).where(Facility.invite_code == code)).scalar_one_or_none() if code else None
        if facility is None:
            raise ApiError(400, "INVALID_INVITE_CODE", "유효하지 않은 초대 코드입니다.")
        user = User(id=user_id, email=email, password_hash=hash_password(password), name=name.strip(),
                    service="FACILITY", role="MEMBER", facility_id=facility.id, onboarded=False)
        db.add(user)
        db.flush()
        _log(db, user, "INFO", f"신규 가입: {user.name} (FACILITY/MEMBER)")
        return user

    # facility_mode == "ROOT" (기본)
    fname = (facility_name or "").strip()
    if not fname:
        raise ApiError(400, "FACILITY_NAME_REQUIRED", "시설명을 입력하세요.")
    facility = Facility(id=uuid.uuid4(), name=fname, invite_code=generate_invite_code(db, fname), root_user_id=user_id)
    user = User(id=user_id, email=email, password_hash=hash_password(password), name=name.strip(),
                service="FACILITY", role="ROOT", facility_id=facility.id, onboarded=False)
    db.add(facility)  # root_user_id FK 는 DEFERRABLE — 커밋 시점에 사용자 행이 있으면 된다
    db.add(user)
    db.flush()
    db.add(TenantConfig(facility_id=facility.id))
    _log(db, user, "INFO", f"신규 가입: {user.name} (FACILITY/ROOT) · 시설 {facility.name} 등록")
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = find_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise ApiError(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    _log(db, user, "INFO", f"로그인: {user.name} ({user.service})")
    return user


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise ApiError(400, "WRONG_PASSWORD", "현재 비밀번호가 올바르지 않습니다.")
    user.password_hash = hash_password(new)
    revoke_all_for_user(db, user.id)
    _log(db, user, "INFO", "비밀번호 변경됨")


# ── 토큰 ────────────────────────────────────────────────────────────
def issue_token_pair(db: Session, user: User, *, family_id: uuid.UUID | None = None,
                     user_agent: str | None = None, ip: str | None = None) -> TokenPair:
    raw = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    row = RefreshToken(
        id=uuid.uuid4(), user_id=user.id, family_id=family_id or uuid.uuid4(), token_hash=_hash_token(raw),
        issued_at=now, expires_at=now + timedelta(days=settings.refresh_ttl_days),
        user_agent=(user_agent or "")[:300] or None, ip=ip,
    )
    db.add(row)
    db.flush()
    access, expires_in = create_access_token(
        user_id=user.id, service=user.service, role=user.role, facility_id=user.facility_id
    )
    return TokenPair(access_token=access, refresh_token=raw, expires_in=expires_in, user=user)


def rotate_refresh(db: Session, raw: str, *, user_agent: str | None = None, ip: str | None = None) -> TokenPair:
    row = db.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw))).scalar_one_or_none()
    if row is None:
        raise unauthorized("REFRESH_INVALID", "세션이 만료되었습니다. 다시 로그인하세요.")
    now = datetime.now(UTC)
    if row.revoked_at is not None or row.replaced_by is not None:
        _revoke_family(db, row.family_id, now)  # 재사용 감지 → 패밀리 전체 폐기
        db.commit()  # 예외를 던지면 get_db 가 롤백하므로 폐기는 먼저 확정한다
        raise unauthorized("REFRESH_REUSED", "세션이 무효화되었습니다. 다시 로그인하세요.")
    if row.expires_at <= now:
        row.revoked_at = now
        db.commit()
        raise unauthorized("REFRESH_EXPIRED", "세션이 만료되었습니다. 다시 로그인하세요.")
    user = db.get(User, row.user_id)
    if user is None:
        raise unauthorized("USER_NOT_FOUND", "계정을 찾을 수 없습니다.")
    pair = issue_token_pair(db, user, family_id=row.family_id, user_agent=user_agent, ip=ip)
    new_row = db.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(pair.refresh_token))).scalar_one()
    row.replaced_by = new_row.id
    row.revoked_at = now
    return pair


def logout(db: Session, raw: str | None, user_id: uuid.UUID) -> None:
    """refresh 를 주면 그 패밀리만, 없으면 사용자의 모든 refresh 를 폐기한다."""
    if raw:
        row = db.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw))).scalar_one_or_none()
        if row is not None and row.user_id == user_id:
            _revoke_family(db, row.family_id, datetime.now(UTC))
            return
    revoke_all_for_user(db, user_id)


def revoke_all_for_user(db: Session, user_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def _revoke_family(db: Session, family_id: uuid.UUID, now: datetime) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
