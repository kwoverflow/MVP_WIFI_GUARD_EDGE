"""Resolve configured recipients and enqueue non-blocking fall notifications."""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy import and_, func, or_, select
from wifiguard_contracts.kafka import InferenceResult
from wifiguard_notify import EmailNotifier, NtfyNotifier

log = logging.getLogger("ingest.fall_notification")


class FallNotificationDispatcher:
    """One notifier thread per recipient channel, created on demand."""

    def __init__(
        self,
        session_factory=None,
        notifier_factory=NtfyNotifier,
        email_notifier_factory=EmailNotifier,
    ) -> None:
        self._session_factory = session_factory
        self._notifier_factory = notifier_factory
        self._email_notifier_factory = email_notifier_factory
        self._notifiers: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._queued = 0
        self._errors = 0
        self._last_error: str | None = None

    def _sessions(self):
        if self._session_factory is not None:
            return self._session_factory()
        from wifiguard_db.engine import get_session

        return get_session()

    def notify(self, result: InferenceResult) -> int:
        """Queue notifications after the corresponding FallEvent was committed."""
        try:
            from wifiguard_db.models import Device, FallEvent, Recipient, Resident, ResidentDevice

            with self._sessions() as db:
                device = db.get(Device, result.device_id)
                if device is None:
                    raise ValueError(f"unknown device {result.device_id}")
                resident = db.execute(
                    select(Resident)
                    .join(ResidentDevice, ResidentDevice.resident_id == Resident.id)
                    .where(ResidentDevice.device_id == result.device_id)
                    .order_by(ResidentDevice.is_primary.desc(), Resident.created_at)
                    .limit(1)
                ).scalar_one_or_none()
                scope = (
                    Recipient.facility_id == device.facility_id
                    if device.facility_id is not None
                    else Recipient.owner_user_id == device.owner_user_id
                )
                resident_filter = (
                    or_(Recipient.resident_id.is_(None), Recipient.resident_id == resident.id)
                    if resident is not None
                    else Recipient.resident_id.is_(None)
                )
                recipients = db.execute(
                    select(Recipient).where(
                        scope,
                        resident_filter,
                        Recipient.enabled.is_(True),
                        or_(
                            and_(Recipient.push.is_(True), Recipient.ntfy_topic.is_not(None)),
                            and_(Recipient.email_enabled.is_(True), Recipient.email.is_not(None)),
                        ),
                    )
                ).scalars().all()
                count_filter = (
                    FallEvent.resident_id == resident.id
                    if resident is not None
                    else FallEvent.device_id == result.device_id
                )
                fall_count = int(
                    db.execute(select(func.count(FallEvent.id)).where(count_filter)).scalar_one()
                )

            queued = 0
            for recipient in recipients:
                recipient_id = str(recipient.id)
                channels: list[tuple[str, Any]] = []
                if recipient.push and recipient.ntfy_topic:
                    channels.append(
                        (
                            f"push:{recipient_id}",
                            lambda: self._notifier_factory(
                                recipient_id=recipient_id,
                                topic=recipient.ntfy_topic,
                                server=recipient.ntfy_server or "https://ntfy.sh",
                                display_name=recipient.name,
                                notify_fall_enabled=True,
                            ),
                        )
                    )
                if recipient.email_enabled and recipient.email:
                    channels.append(
                        (
                            f"email:{recipient_id}",
                            lambda: self._email_notifier_factory(
                                recipient_id=recipient_id,
                                email=recipient.email,
                                display_name=recipient.name,
                                notify_fall_enabled=True,
                            ),
                        )
                    )
                for key, factory in channels:
                    with self._lock:
                        notifier = self._notifiers.get(key)
                        if notifier is None:
                            notifier = factory()
                            notifier.start()
                            self._notifiers[key] = notifier
                    notifier.notify_fall(fall_count, result.proba_fall, result.ts.timestamp())
                    queued += 1
            with self._lock:
                self._queued += queued
                self._last_error = None
            return queued
        except Exception as exc:
            with self._lock:
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("fall notification dispatch failed: %s", exc)
            return 0

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            notifiers = list(self._notifiers.values())
        for notifier in notifiers:
            notifier.stop()
        for notifier in notifiers:
            notifier.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "recipients_active": len(self._notifiers),
                "queued": self._queued,
                "errors": self._errors,
                "last_error": self._last_error,
                "notifiers": [notifier.status() for notifier in self._notifiers.values()],
            }
