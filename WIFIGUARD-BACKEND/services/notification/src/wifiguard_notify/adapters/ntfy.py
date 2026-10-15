"""ntfy 기반 낙상 푸시 알림 발송 모듈 (단계 5).

FALL 확정 시 ntfy 서버(기본 ntfy.sh)의 토픽으로 알림을 발송한다.
휴대폰은 ntfy 앱(iOS/Android)에서 같은 토픽을 구독하면 푸시를 받는다.

설계:
- 발송은 전용 스레드 + 큐로 처리해 탐지 루프(0.25초 스트라이드)를 막지 않는다.
- 실패 시 지수 백오프(1, 2, 4초)로 최대 3회 재시도한다.
- 표준 라이브러리 urllib만 사용한다 (추가 의존성 없음).
- 한글 제목/본문을 위해 헤더 방식 대신 JSON 발행(서버 루트 POST)을 쓴다.

주의: ntfy.sh 공개 서버의 토픽은 이름을 아는 누구나 구독/발행할 수 있다.
추측 불가능한 토픽명을 쓰고, 제품화 단계에서는 셀프호스트 또는 접근 토큰을 검토한다.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("notifier")

DEFAULT_SERVER = "https://ntfy.sh"
MAX_ATTEMPTS = 3
BACKOFF_BASE_SEC = 1.0
REQUEST_TIMEOUT_SEC = 5.0
QUEUE_MAXSIZE = 32


class NtfyNotifier(threading.Thread):
    """ntfy 토픽으로 알림을 비동기 발송하는 백그라운드 스레드."""

    def __init__(
        self,
        recipient_id: str,
        topic: str,
        server: str = DEFAULT_SERVER,
        display_name: str | None = None,
        notify_fall_enabled: bool = True,
    ) -> None:
        # threading.Thread.name은 프로퍼티라 재사용하면 스레드 이름을 덮어써
        # 버리므로, 수신자 표시 이름은 별도 속성(display_name)에 둔다.
        super().__init__(daemon=True, name=f"ntfy-notifier-{recipient_id}")
        self.id = recipient_id
        self.display_name = display_name
        self.topic = topic
        self.server = server.rstrip("/")
        self.notify_fall_enabled = notify_fall_enabled
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=QUEUE_MAXSIZE)
        # Thread._stop() is an internal method used by join(); never shadow it.
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._sent_count = 0
        self._failed_count = 0
        self._dropped_count = 0
        self._last_sent_time: float | None = None
        self._last_error: str | None = None

    # ---- 발송 요청 (탐지 루프 등 호출측 스레드에서 실행, 블로킹 금지) ----

    def notify_fall(self, fall_count: int, proba: float | None, at: float) -> None:
        """FALL 확정 시 호출. 큐에 넣기만 하고 즉시 반환한다."""
        if not self.notify_fall_enabled:
            return
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(at))
        proba_text = f"{proba:.1%}" if proba is not None else "N/A"
        self._enqueue(
            {
                "topic": self.topic,
                "title": "낙상 감지",
                "message": f"낙상이 감지되었습니다.\n시각: {when}\n확률: {proba_text} (누적 {fall_count}회)",
                "priority": 5,
                "tags": ["rotating_light"],
            }
        )

    def notify_test(self) -> None:
        """대시보드/운영자 수동 테스트 발송."""
        when = time.strftime("%Y-%m-%d %H:%M:%S")
        self._enqueue(
            {
                "topic": self.topic,
                "title": "CSI-Guard 테스트 알림",
                "message": f"알림 경로 정상 동작 확인 ({when})",
                "priority": 3,
                "tags": ["white_check_mark"],
            }
        )

    def send_test_now(self) -> bool:
        """Send a test synchronously for the authenticated API test endpoint."""
        when = time.strftime("%Y-%m-%d %H:%M:%S")
        return self._send_with_retry(
            {
                "topic": self.topic,
                "title": "CSI-Guard 테스트 알림",
                "message": f"휴대폰 푸시 경로 정상 동작 확인 ({when})",
                "priority": 3,
                "tags": ["white_check_mark"],
            }
        )

    def _enqueue(self, payload: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            with self._lock:
                self._dropped_count += 1
            log.error("알림 큐 가득 참, 발송 폐기: %s", payload.get("title"))

    # ---- 발송 스레드 ----

    def run(self) -> None:
        log.info("notifier start: server=%s topic=%s", self.server, self.topic)
        while not self._stop_event.is_set():
            try:
                payload = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if payload is None:
                self._queue.task_done()
                break
            try:
                self._send_with_retry(payload)
            finally:
                self._queue.task_done()

    def stop(self) -> None:
        self._stop_event.set()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)

    def _send_with_retry(self, payload: dict[str, Any]) -> bool:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                request = urllib.request.Request(
                    self.server,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as response:
                    response.read()
                with self._lock:
                    self._sent_count += 1
                    self._last_sent_time = time.time()
                    self._last_error = None
                log.info("알림 발송 성공: %s (시도 %d)", payload.get("title"), attempt)
                return True
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
                last_error = f"{type(error).__name__}: {error}"
                log.warning("알림 발송 실패 (시도 %d/%d): %s", attempt, MAX_ATTEMPTS, last_error)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_BASE_SEC * (2 ** (attempt - 1)))
        with self._lock:
            self._failed_count += 1
            self._last_error = last_error
        log.error("알림 발송 최종 실패: %s", payload.get("title"))
        return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "channel": "push",
                "name": self.display_name,
                "enabled": True,
                "notify_fall_enabled": self.notify_fall_enabled,
                "server": self.server,
                "topic": self.topic,
                "sent_count": self._sent_count,
                "failed_count": self._failed_count,
                "dropped_count": self._dropped_count,
                "last_sent_time": self._last_sent_time,
                "last_error": self._last_error,
            }
