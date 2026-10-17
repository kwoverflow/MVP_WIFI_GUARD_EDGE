from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from wifiguard_contracts.api import ErrorOut, RecipientIn, RecipientOut, RecipientPatch
from wifiguard_db.models import Recipient, Resident
from wifiguard_notify import EmailNotifier, NtfyNotifier

from ..deps import Scope, add_log, assert_in_scope, get_db, get_scope, scope_filter, scoped_get, stamp_scope
from ..errors import ApiError

router = APIRouter(prefix="/recipients", tags=["recipients"])
_ERR = {401: {"model": ErrorOut}, 404: {"model": ErrorOut}, 400: {"model": ErrorOut}}


@router.get("", response_model=list[RecipientOut], responses=_ERR)
def list_recipients(
    resident_id: uuid.UUID | None = Query(default=None, alias="residentId"),
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
) -> list[Recipient]:
    where = [scope_filter(Recipient, scope)]
    if resident_id:
        where.append(Recipient.resident_id == resident_id)
    return list(db.execute(select(Recipient).where(*where).order_by(Recipient.created_at)).scalars())


@router.post("", response_model=RecipientOut, status_code=status.HTTP_201_CREATED, responses=_ERR)
def create_recipient(body: RecipientIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Recipient:
    _validate_channels(body)
    if body.resident_id:
        assert_in_scope(db, Resident, [body.resident_id], scope, field="residentId")
    rec = Recipient(**body.model_dump())
    rec.name = rec.name.strip()
    stamp_scope(rec, scope)
    db.add(rec)
    db.flush()
    add_log(db, scope, "INFO", f"알림 수신자 등록: {rec.name}", resident_id=rec.resident_id)
    db.commit()
    return rec


@router.patch("/{recipient_id}", response_model=RecipientOut, responses=_ERR)
def patch_recipient(recipient_id: uuid.UUID, body: RecipientPatch, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Recipient:
    rec = scoped_get(db, Recipient, recipient_id, scope, what="수신자")
    data = body.model_dump(exclude_unset=True)
    data.pop("clear_resident", None)
    if "resident_id" in data and data["resident_id"] is not None:
        assert_in_scope(db, Resident, [data["resident_id"]], scope, field="residentId")
    for key, value in data.items():
        if value is None and key != "resident_id":
            continue
        setattr(rec, key, value.strip() if isinstance(value, str) else value)
    if body.clear_resident:
        rec.resident_id = None
    _validate_recipient(rec)
    db.commit()
    return rec


@router.delete("/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERR)
def delete_recipient(recipient_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Response:
    rec = scoped_get(db, Recipient, recipient_id, scope, what="수신자")
    add_log(db, scope, "INFO", f"알림 수신자 삭제: {rec.name}")
    db.delete(rec)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{recipient_id}/test", responses={**_ERR, 502: {"model": ErrorOut}})
def test_recipient(
    recipient_id: uuid.UUID,
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
) -> dict:
    """Synchronously verify every configured channel for one recipient."""
    rec = scoped_get(db, Recipient, recipient_id, scope, what="수신자")
    _validate_recipient(rec)
    if not rec.enabled:
        raise ApiError(400, "RECIPIENT_DISABLED", "비활성 수신자는 테스트할 수 없습니다.")

    results: list[dict[str, object]] = []
    if rec.email_enabled and rec.email:
        notifier = EmailNotifier(str(rec.id), rec.email, display_name=rec.name)
        ok = notifier.send_test_now()
        results.append({"channel": "email", "ok": ok, "target": rec.email})
    if rec.push and rec.ntfy_topic:
        notifier = NtfyNotifier(
            str(rec.id),
            rec.ntfy_topic,
            server=rec.ntfy_server or "https://ntfy.sh",
            display_name=rec.name,
        )
        ok = notifier.send_test_now()
        results.append({"channel": "push", "ok": ok, "target": rec.ntfy_topic})
    if not results:
        raise ApiError(400, "NO_ACTIVE_CHANNEL", "이메일 또는 휴대폰 푸시 채널을 먼저 설정하세요.")
    failures = [item for item in results if not item["ok"]]
    if failures:
        raise ApiError(
            502,
            "NOTIFICATION_DELIVERY_FAILED",
            "일부 알림 채널의 테스트 발송에 실패했습니다.",
            {"channels": results},
        )
    add_log(db, scope, "INFO", f"알림 테스트 발송 성공: {rec.name}")
    db.commit()
    return {"ok": True, "channels": results}


def _validate_channels(body: RecipientIn) -> None:
    from .pc_simulation import validate_pc_notification_target
    validate_pc_notification_target(body)
    if body.email_enabled and not (body.email or "").strip():
        raise ApiError(400, "EMAIL_REQUIRED", "이메일 알림을 사용하려면 이메일 주소가 필요합니다.")
    if body.push and not (body.ntfy_topic or "").strip():
        raise ApiError(400, "NTFY_TOPIC_REQUIRED", "휴대폰 푸시를 사용하려면 ntfy 구독 코드가 필요합니다.")


def _validate_recipient(rec: Recipient) -> None:
    from .pc_simulation import validate_pc_notification_target
    validate_pc_notification_target(rec)
    if rec.email_enabled and not (rec.email or "").strip():
        raise ApiError(400, "EMAIL_REQUIRED", "이메일 알림을 사용하려면 이메일 주소가 필요합니다.")
    if rec.push and not (rec.ntfy_topic or "").strip():
        raise ApiError(400, "NTFY_TOPIC_REQUIRED", "휴대폰 푸시를 사용하려면 ntfy 구독 코드가 필요합니다.")
