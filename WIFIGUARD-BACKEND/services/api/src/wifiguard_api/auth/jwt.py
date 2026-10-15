"""Access 토큰 (PyJWT, HS256). 클레임: sub, svc, role, fid, iat, exp, jti, typ."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from ..config import settings

ALGORITHM = "HS256"


class AccessTokenError(Exception):
    pass


def create_access_token(*, user_id: uuid.UUID, service: str, role: str, facility_id: uuid.UUID | None) -> tuple[str, int]:
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.access_ttl_min)
    payload = {
        "sub": str(user_id),
        "svc": service,
        "role": role,
        "fid": str(facility_id) if facility_id else None,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), int(ttl.total_seconds())


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp", "iat", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AccessTokenError("TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise AccessTokenError("TOKEN_INVALID") from exc
    if payload.get("typ") != "access":
        raise AccessTokenError("TOKEN_INVALID")
    return payload
