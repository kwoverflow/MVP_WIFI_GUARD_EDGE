"""텔레메트리 → `devices.online` / `devices.last_seen_at` 갱신.

이 값들은 지금까지 **아무도 쓰지 않았다.** 프론트가 읽기만 하고 갱신 주체가 없어서
기기를 등록하면 영원히 `online=false` 로 표시됐다.

## 매 텔레메트리마다 UPDATE 하지 않는다

telemetry 는 1Hz(퇴실 시 0.2Hz)다. 기기 10대면 초당 10회 UPDATE 인데, 화면이 필요로 하는
정밀도는 그만큼이 아니다. `min_write_interval_s` 마다만 DB 에 쓰고, 그 사이 값은
`LiveCache` 가 들고 있는다(REST 응답과 WS 는 캐시를 본다).

상태가 **바뀌는 순간**(online false→true)은 주기와 무관하게 즉시 쓴다. 화면의 "연결됨"이
최대 30초 늦게 뜨면 안 되기 때문이다.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any
from uuid import UUID

log = logging.getLogger("ingest.telemetry_sink")

#: 같은 기기에 대한 DB 쓰기 최소 간격. 그 사이 값은 LiveCache 가 제공한다.
MIN_WRITE_INTERVAL_S = 30.0


class TelemetrySink:
    def __init__(self, min_write_interval_s: float = MIN_WRITE_INTERVAL_S) -> None:
        self.min_write_interval_s = min_write_interval_s
        self._lock = threading.Lock()
        self._last_write: dict[UUID, float] = {}
        self._last_online: dict[UUID, bool] = {}
        self._written = 0
        self._skipped = 0
        self._errors = 0
        self._last_error: str | None = None

    def _should_write(self, device_id: UUID, online: bool) -> bool:
        now = time.monotonic()
        with self._lock:
            changed = self._last_online.get(device_id) != online
            due = (now - self._last_write.get(device_id, 0.0)) >= self.min_write_interval_s
            if changed or due:
                self._last_write[device_id] = now
                self._last_online[device_id] = online
                return True
            self._skipped += 1
            return False

    def apply(self, device_id: UUID, last_seen_at: datetime, online: bool = True) -> None:
        """컨슈머 스레드에서 호출된다. DB 쓰기는 짧고, 실패해도 컨슈머를 죽이지 않는다."""
        if not self._should_write(device_id, online):
            return
        try:
            from sqlalchemy import update
            from wifiguard_db.engine import get_session
            from wifiguard_db.models import Device

            with get_session() as db:
                db.execute(
                    update(Device)
                    .where(Device.id == device_id)
                    .values(online=online, last_seen_at=last_seen_at)
                )
            with self._lock:
                self._written += 1
        except Exception as exc:
            with self._lock:
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("device %s 텔레메트리 반영 실패: %s", device_id, exc)

    def mark_offline(self, device_id: UUID) -> None:
        """스윕이 끊긴 기기를 발견했을 때. `last_seen_at` 은 건드리지 않는다 —
        마지막으로 **본** 시각이지 지금 시각이 아니다."""
        if not self._should_write(device_id, False):
            return
        try:
            from sqlalchemy import update
            from wifiguard_db.engine import get_session
            from wifiguard_db.models import Device

            with get_session() as db:
                db.execute(update(Device).where(Device.id == device_id).values(online=False))
            with self._lock:
                self._written += 1
        except Exception as exc:
            with self._lock:
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "written": self._written,
                "skipped": self._skipped,
                "errors": self._errors,
                "last_error": self._last_error,
            }
