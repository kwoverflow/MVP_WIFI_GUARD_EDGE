"""`presence_samples` 배치 적재 — **TimescaleDB 인스턴스는 메인 DB 와 다르다.**

`presence_samples` 는 `TSDB_DSN`(별도 인스턴스, 포트 5433, DB `wifiguard_ts`)에 있고
`devices`/`residents`/`fall_events` 는 `DATABASE_URL`(메인 postgres) 에 있다.
`routers/health.py:70-78` 이 둘을 따로 점검하는 것이 그 증거다.

그래서 `wifiguard_db.engine`(SQLAlchemy, DATABASE_URL)로는 이 테이블에 닿을 수 없다.
psycopg 로 직접 연결하며, **트랜잭션이 두 DB 에 걸치지 않게** 한다.

컬럼명은 `wifiguard_contracts.mqtt.PresenceMsg` 의 상태 11필드와 **문자 단위로 같다**
(계약 파리티 테스트가 강제한다). 그래서 매핑 dict 가 없고, 있으면 안 된다.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from typing import Any

from wifiguard_contracts.mqtt import PRESENCE_STATUS_FIELDS

log = logging.getLogger("ingest.presence_sink")

#: 메타 3 + 상태 11 = 14 컬럼. 순서가 INSERT 문과 값 튜플에서 같아야 한다.
COLUMNS: tuple[str, ...] = ("ts", "facility_id", "device_id", *PRESENCE_STATUS_FIELDS)

_INSERT = (
    f"INSERT INTO presence_samples ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(COLUMNS))})"
)


class PresenceSink:
    """4Hz × 기기 수를 배치로 모아 넣는다. 건별 INSERT 는 왕복 비용이 지배적이다."""

    def __init__(self, dsn: str, batch_size: int = 200, flush_interval_s: float = 1.0) -> None:
        self.dsn = dsn
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self._queue: queue.Queue[tuple] = queue.Queue(maxsize=batch_size * 20)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._written = 0
        self._dropped = 0
        self._errors = 0
        self._last_error: str | None = None

    # ── 수명 ────────────────────────────────────────────────────────
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="presence-sink")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ── 입력 ────────────────────────────────────────────────────────
    def offer(self, ts: datetime, tenant_id: str, device_id: str, fields: dict[str, Any]) -> None:
        """컨슈머 스레드가 호출한다. **막히지 않는다** — 큐가 차면 버리고 센다.

        재실 시계열은 유실돼도 서비스가 멈추지 않는다. 반대로 여기서 블로킹하면
        Kafka 컨슈머가 밀리고 결국 실시간 화면까지 늦어진다.
        """
        row = (ts, tenant_id, device_id, *(fields.get(name) for name in PRESENCE_STATUS_FIELDS))
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("presence 적재 큐 포화 — 누적 %d 건 폐기", self._dropped)

    # ── 적재 ────────────────────────────────────────────────────────
    def _run(self) -> None:
        import psycopg

        conn: Any = None
        batch: list[tuple] = []
        last_flush = time.monotonic()

        while not self._stop.is_set() or not self._queue.empty() or batch:
            try:
                timeout = max(0.05, self.flush_interval_s / 4)
                batch.append(self._queue.get(timeout=timeout))
            except queue.Empty:
                pass

            due = (
                len(batch) >= self.batch_size
                or (batch and (time.monotonic() - last_flush) >= self.flush_interval_s)
                or (batch and self._stop.is_set() and self._queue.empty())
            )
            if not due:
                continue

            try:
                if conn is None or conn.closed:
                    conn = psycopg.connect(self.dsn, autocommit=False, connect_timeout=5)
                    log.info("TimescaleDB 연결")
                with conn.cursor() as cur:
                    cur.executemany(_INSERT, batch)
                conn.commit()
                with self._lock:
                    self._written += len(batch)
                batch.clear()
                last_flush = time.monotonic()
            except Exception as exc:
                with self._lock:
                    self._errors += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning("presence 적재 실패 (%d건 폐기): %s", len(batch), exc)
                # 재시도하지 않고 버린다. 시계열은 최신성이 전부이고, 밀린 배치를
                # 붙들고 있으면 큐가 차서 새 데이터까지 잃는다.
                batch.clear()
                last_flush = time.monotonic()
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                time.sleep(1.0)

        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "written": self._written,
                "dropped": self._dropped,
                "errors": self._errors,
                "queued": self._queue.qsize(),
                "last_error": self._last_error,
            }
