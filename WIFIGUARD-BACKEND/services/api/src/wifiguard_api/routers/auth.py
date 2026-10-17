from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from wifiguard_contracts.api import (
    ErrorOut,
    Features,
    FacilityOut,
    LoginIn,
    LogoutIn,
    MeOut,
    RefreshIn,
    SignupIn,
    TokenPair,
    UserOut,
)
from wifiguard_db.models import Facility

from ..auth import service as auth_service
from ..config import settings
from ..deps import Scope, client_meta, get_db, get_scope

router = APIRouter(prefix="/auth", tags=["auth"])

_ERR = {401: {"model": ErrorOut}, 400: {"model": ErrorOut}, 409: {"model": ErrorOut}}


def _pair_out(pair: auth_service.TokenPair) -> TokenPair:
    return TokenPair(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        user=UserOut.model_validate(pair.user),
    )


@router.post("/signup", response_model=TokenPair, status_code=status.HTTP_201_CREATED, responses=_ERR)
def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    user = auth_service.signup(
        db,
        email=body.email,
        password=body.password,
        name=body.name,
        service=body.service,
        facility_mode=body.facility_mode,
        facility_name=body.facility_name,
        invite_code=body.invite_code,
    )
    ua, ip = client_meta(request)
    return _pair_out(auth_service.issue_token_pair(db, user, user_agent=ua, ip=ip))


@router.post("/login", response_model=TokenPair, responses=_ERR)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    user = auth_service.authenticate(db, body.email, body.password)
    ua, ip = client_meta(request)
    return _pair_out(auth_service.issue_token_pair(db, user, user_agent=ua, ip=ip))


@router.post("/refresh", response_model=TokenPair, responses=_ERR)
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    ua, ip = client_meta(request)
    return _pair_out(auth_service.rotate_refresh(db, body.refresh_token, user_agent=ua, ip=ip))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses={401: {"model": ErrorOut}})
def logout(body: LogoutIn | None = None, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Response:
    auth_service.logout(db, body.refresh_token if body else None, scope.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeOut, responses={401: {"model": ErrorOut}})
def me(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> MeOut:
    facility = db.get(Facility, scope.facility_id) if scope.facility_id else None
    return MeOut(
        **UserOut.model_validate(scope.user).model_dump(),
        facility=FacilityOut.model_validate(facility) if facility else None,
        features=Features(fall_simulate=settings.allow_fall_simulate),
    )
