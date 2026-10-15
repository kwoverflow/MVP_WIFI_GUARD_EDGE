"""FCM HTTP v1 and a bounded, durable outbox. Disabled by default in PC fixtures.

Explicit, time-bounded PC test mode permits only manually requested TEST messages;
PC fall events remain blocked at enqueue and dispatch.

FCM acceptance is NOT phone receipt or an operator acknowledgement. At-least-once
retry can deliver duplicates; the app deduplicates on deliveryId. No raw CSI,
resident names, room names, account credentials or JWTs leave in push payloads.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import uuid
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("notifier.mobile")
TTL_SECONDS = 600
MAX_ATTEMPTS = 5
MAX_PC_TEST_SECONDS = 3600


def enabled() -> bool:
    return os.getenv("MOBILE_PUSH_ENABLED") == "1" and os.getenv("PC_SIMULATION_MODE") != "1"


def pc_test_push_enabled() -> bool:
    if (os.getenv("PC_SIMULATION_MODE") != "1" or os.getenv("MOBILE_PUSH_ENABLED") != "1"
            or os.getenv("MOBILE_PC_TEST_PUSH_ENABLED") != "1"):
        return False
    try:
        remaining = float(os.getenv("MOBILE_PC_TEST_PUSH_EXPIRES_AT", "")) - time.time()
        return math.isfinite(remaining) and 0 < remaining <= MAX_PC_TEST_SECONDS
    except (ValueError, TypeError):
        return False


def test_push_enabled() -> bool:
    return enabled() or pc_test_push_enabled()


def authorized(installation, user, fall=None) -> bool:
    if not installation.enabled or user is None or installation.user_id != user.id:
        return False
    if user.service == "FACILITY":
        return bool(user.facility_id and installation.facility_id == user.facility_id
                    and (fall is None or fall.facility_id == user.facility_id))
    return installation.facility_id is None and (fall is None or fall.owner_user_id == user.id)


def enqueue_fall(db, fall) -> int:
    """Called INSIDE the FallEvent transaction, before commit. Failures roll back both."""
    if not enabled() or fall.source != "EDGE":
        return 0  # Synthetic PC events must never leave the local fixture.
    from sqlalchemy import select
    from wifiguard_db.models import MobileInstallation, User
    count = 0
    for installation, user in db.execute(select(MobileInstallation, User).join(User, User.id == MobileInstallation.user_id).where(
            MobileInstallation.enabled.is_(True),
            MobileInstallation.facility_id == fall.facility_id if fall.facility_id else MobileInstallation.user_id == fall.owner_user_id)):
        if authorized(installation, user, fall):
            enqueue(db, installation.id, "FALL", fall.id)
            count += 1
    return count


def enqueue(db, installation_id, kind, fall_id=None):
    from wifiguard_db.models import MobilePushDelivery
    now = datetime.now(UTC)
    delivery = MobilePushDelivery(id=uuid.uuid4(), installation_id=installation_id,
                                 fall_event_id=fall_id, kind=kind, state="PENDING", attempts=0,
                                 created_at=now, expires_at=now + timedelta(seconds=TTL_SECONDS), next_attempt_at=now)
    db.add(delivery)
    return delivery


class PushFailure(Exception):
    def __init__(self, code: str, *, permanent=False, invalidate=False):
        super().__init__(code)  # Never include HTTP bodies/tokens in diagnostics.
        self.code, self.permanent, self.invalidate = code, permanent, invalidate


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward server credentials to a redirected endpoint.


class FcmTransport:
    def __init__(self):
        from google.auth.transport.requests import Request
        from google.oauth2.service_account import Credentials
        project = os.getenv("FCM_PROJECT_ID", "")
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", project):
            raise RuntimeError("Set FCM_PROJECT_ID to the Firebase project ID")
        path = Path(os.environ.get("FCM_SERVICE_ACCOUNT_FILE", ""))
        if not path.is_file():
            raise RuntimeError("FCM_SERVICE_ACCOUNT_FILE must point to a private server-only JSON key")
        credentials = Credentials.from_service_account_file(str(path), scopes=["https://www.googleapis.com/auth/firebase.messaging"])
        if credentials.project_id != project:
            raise RuntimeError("FCM service account project must match FCM_PROJECT_ID")
        self.credentials, self.auth_request = credentials, Request()
        self.endpoint = f"https://fcm.googleapis.com/v1/projects/{project}/messages:send"
        self.opener = urllib.request.build_opener(_NoRedirect())

    def send(self, token, data, ttl_seconds):
        if not self.credentials.valid:
            try:
                self.credentials.refresh(lambda *args, **kwargs: self.auth_request(*args, **{**kwargs, "timeout": 10}))
            except Exception as exc:
                raise PushFailure("FCM_AUTH_FAILED") from exc
        body = json.dumps({"message": {"token": token, "data": data,
                           "android": {"priority": "high", "ttl": f"{ttl_seconds}s"}}}).encode()
        request = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.credentials.token}", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=10) as response:
                result = json.loads(response.read(65536))
            if not result.get("name"):
                raise PushFailure("FCM_BAD_RESPONSE")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                self.credentials.token = None  # Refresh OAuth credentials on the next bounded retry.
            code = ""
            try:
                error = json.loads(exc.read(65536)).get("error", {})
                code = next((item.get("errorCode", "") for item in error.get("details", []) if "errorCode" in item), "")
            except (ValueError, AttributeError, TypeError):
                pass
            raise PushFailure(f"FCM_HTTP_{exc.code}", permanent=exc.code in (400, 403, 404), invalidate=code == "UNREGISTERED") from exc
        except (OSError, ValueError) as exc:
            raise PushFailure("FCM_TRANSPORT_ERROR") from exc


class MobilePushWorker(threading.Thread):
    def __init__(self, session_factory, transport):
        super().__init__(name="mobile-push-outbox", daemon=True)
        self.sessions, self.transport = session_factory, transport
        self.stop_event = threading.Event()

    def drain_one(self) -> bool:
        from sqlalchemy import select
        from wifiguard_db.models import MobileInstallation, MobilePushDelivery, User, FallEvent
        now = datetime.now(UTC)
        with self.sessions() as db:
            row = db.execute(select(MobilePushDelivery).where(MobilePushDelivery.state == "PENDING", MobilePushDelivery.next_attempt_at <= now)
                             .order_by(MobilePushDelivery.created_at).with_for_update(skip_locked=True).limit(1)).scalar_one_or_none()
            if row is None:
                return False
            installation = db.get(MobileInstallation, row.installation_id)
            user = db.get(User, installation.user_id) if installation else None
            fall = db.get(FallEvent, row.fall_event_id) if row.fall_event_id else None
            expiry = row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
            if not installation or not authorized(installation, user, fall):
                row.state, row.last_error = "REVOKED", "RECIPIENT_REVOKED"
            elif expiry <= now:
                row.state, row.last_error = "FAILED", "PUSH_EXPIRED"
            elif os.getenv("PC_SIMULATION_MODE") == "1" and not (
                    pc_test_push_enabled() and row.kind == "TEST" and row.fall_event_id is None):
                row.state, row.last_error = "REVOKED", "PC_EXTERNAL_PUSH_BLOCKED"
            else:
                row.attempts += 1
                data = {"deliveryId": str(row.id), "installationId": str(installation.installation_id),
                        "kind": row.kind, "fallEventId": str(row.fall_event_id or ""),
                        "expiresAt": str(int(expiry.timestamp())),
                        "title": "WIFI-GUARD 낙상 알림" if row.kind == "FALL" else "WIFI-GUARD 시험 알림",
                        "body": "앱을 열어 알림 내용을 확인하세요."}
                try:
                    self.transport.send(installation.fcm_token, data, max(1, int((expiry - now).total_seconds())))
                    row.state, row.accepted_at, row.last_error = "ACCEPTED", now, None
                except PushFailure as exc:
                    row.last_error = exc.code
                    if exc.invalidate:
                        installation.enabled = False
                    row.state = "FAILED" if exc.permanent or row.attempts >= MAX_ATTEMPTS else "PENDING"
                    row.next_attempt_at = now + timedelta(seconds=min(60, 2 ** row.attempts))
                except Exception:
                    row.last_error = "PUSH_INTERNAL_ERROR"
                    row.state = "FAILED" if row.attempts >= MAX_ATTEMPTS else "PENDING"
                    row.next_attempt_at = now + timedelta(seconds=30)
            db.commit()
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                worked = self.drain_one()
            except Exception:
                log.error("mobile outbox unavailable; retrying without exposing credentials")
                worked = False
            self.stop_event.wait(.05 if worked else 1)

    def stop(self):
        self.stop_event.set()
        self.join(timeout=15)
