"""Persist confirmed model transitions as idempotent ``fall_events`` rows."""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy import select
from wifiguard_contracts.kafka import InferenceResult

log = logging.getLogger("ingest.fall_sink")


class FallEventSink:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._written = 0
        self._duplicates = 0
        self._errors = 0
        self._last_error: str | None = None

    def _sessions(self):
        if self._session_factory is not None:
            return self._session_factory()
        from wifiguard_db.engine import get_session

        return get_session()

    @staticmethod
    def event_key(result: InferenceResult) -> str:
        return f"{result.device_id}:{result.ts.isoformat()}:{result.seq}"

    def event_id(self, result: InferenceResult):
        """Resolve the committed row ID for UI acknowledgement, never a live ID."""
        from wifiguard_db.models import FallEvent
        with self._sessions() as db:
            return db.execute(select(FallEvent.id).where(
                FallEvent.source_event_key == self.event_key(result),
                FallEvent.device_id == result.device_id,
            )).scalar_one_or_none()

    def record(self, result: InferenceResult) -> bool:
        """Write once. Missing devices or tenant mismatches are rejected, not fabricated."""
        try:
            from wifiguard_db.models import Device, EventLog, FallEvent, Resident, ResidentDevice

            key = self.event_key(result)
            with self._sessions() as db:
                if db.execute(
                    select(FallEvent.id).where(FallEvent.source_event_key == key)
                ).scalar_one_or_none() is not None:
                    with self._lock:
                        self._duplicates += 1
                    return False

                device = db.get(Device, result.device_id)
                if device is None:
                    raise ValueError(f"unknown device {result.device_id}")
                expected_tenant = (
                    str(device.facility_id)
                    if device.facility_id is not None
                    else f"home-{device.owner_user_id}"
                )
                if result.tenant_id != expected_tenant:
                    raise ValueError(
                        f"tenant mismatch for {result.device_id}: "
                        f"{result.tenant_id!r} != {expected_tenant!r}"
                    )

                resident = db.execute(
                    select(Resident)
                    .join(ResidentDevice, ResidentDevice.resident_id == Resident.id)
                    .where(ResidentDevice.device_id == result.device_id)
                    .order_by(ResidentDevice.is_primary.desc(), Resident.created_at)
                    .limit(1)
                ).scalar_one_or_none()
                fall = FallEvent(
                    resident_id=resident.id if resident else None,
                    resident_name=resident.name if resident else device.name,
                    room=resident.room if resident else device.room,
                    device_id=device.id,
                    occurred_at=result.ts,
                    confidence=result.proba_fall,
                    duration_s=0.0,
                    source="SIMULATED" if (result.model_version or "").startswith("pc-injected-alert-routing-test-") else "EDGE",
                    response="PENDING",
                    source_event_key=key,
                    model_version=result.model_version,
                    facility_id=device.facility_id,
                    owner_user_id=device.owner_user_id,
                )
                db.add(fall)
                db.flush()
                # Mobile delivery is durable and commits atomically with the fall.
                # Existing email/ntfy dispatch after commit remains unchanged.
                from wifiguard_notify.mobile import enqueue_fall
                enqueue_fall(db, fall)
                db.add(
                    EventLog(
                        ts=result.inferred_at,
                        level="FALL",
                        msg=(
                            ("[PC 시험 이벤트 · 모델 판정 아님] " if fall.source == "SIMULATED" else "") +
                            f"{fall.resident_name} ({fall.room}) 낙상 감지 · "
                            f"신뢰도 {fall.confidence:.3f}"
                        ),
                        resident_id=fall.resident_id,
                        device_id=device.id,
                        fall_event_id=fall.id,
                        facility_id=device.facility_id,
                        owner_user_id=device.owner_user_id,
                    )
                )
            with self._lock:
                self._written += 1
                self._last_error = None
            return True
        except Exception as exc:
            with self._lock:
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("fall event persistence failed: %s", exc)
            return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "written": self._written,
                "duplicates": self._duplicates,
                "errors": self._errors,
                "last_error": self._last_error,
            }
