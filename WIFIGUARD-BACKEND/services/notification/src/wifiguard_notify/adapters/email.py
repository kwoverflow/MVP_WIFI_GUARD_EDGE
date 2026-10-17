"""SMTP based email notifications.

The adapter mirrors :class:`NtfyNotifier`: producers only enqueue work, while a
dedicated daemon thread performs network I/O and retries.  ``send_test_now`` is
provided for the authenticated recipient test endpoint so it can return an
honest delivery result instead of a fire-and-forget 202.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from typing import Any

log = logging.getLogger("email_notifier")

MAX_ATTEMPTS = 3
BACKOFF_BASE_SEC = 1.0
QUEUE_MAXSIZE = 32


class EmailNotifier(threading.Thread):
    """Send fall notifications to one email recipient without blocking ingest."""

    def __init__(
        self,
        recipient_id: str,
        email: str,
        display_name: str | None = None,
        *,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        smtp_from: str | None = None,
        smtp_starttls: bool | None = None,
        notify_fall_enabled: bool = True,
    ) -> None:
        super().__init__(daemon=True, name=f"email-notifier-{recipient_id}")
        self.id = recipient_id
        self.email = email
        self.display_name = display_name
        self.smtp_host = smtp_host or os.environ.get("SMTP_HOST", "")
        self.smtp_port = smtp_port or int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_username = smtp_username if smtp_username is not None else os.environ.get("SMTP_USERNAME", "")
        self.smtp_password = smtp_password if smtp_password is not None else os.environ.get("SMTP_PASSWORD", "")
        self.smtp_from = smtp_from or os.environ.get("SMTP_FROM", "alerts@wifiguard.local")
        self.smtp_starttls = (
            smtp_starttls
            if smtp_starttls is not None
            else os.environ.get("SMTP_STARTTLS", "1").lower() in {"1", "true", "yes"}
        )
        self.notify_fall_enabled = notify_fall_enabled
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._sent_count = 0
        self._failed_count = 0
        self._dropped_count = 0
        self._last_sent_time: float | None = None
        self._last_error: str | None = None

    def notify_fall(self, fall_count: int, proba: float | None, at: float) -> None:
        if not self.notify_fall_enabled:
            return
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(at))
        probability = f"{proba:.1%}" if proba is not None else "N/A"
        self._enqueue(
            {
                "subject": "[긴급] WIFI-Guard 낙상 감지",
                "body": (
                    "WIFI-Guard에서 낙상이 감지되었습니다.\n\n"
                    f"감지 시각: {when}\n"
                    f"낙상 확률: {probability}\n"
                    f"누적 감지: {fall_count}회\n\n"
                    "대상자의 상태를 즉시 확인해 주세요."
                ),
            }
        )

    def notify_test(self) -> None:
        when = time.strftime("%Y-%m-%d %H:%M:%S")
        self._enqueue(
            {
                "subject": "WIFI-Guard 이메일 테스트",
                "body": f"이메일 알림 경로가 정상 동작합니다.\n확인 시각: {when}",
            }
        )

    def send_test_now(self) -> bool:
        when = time.strftime("%Y-%m-%d %H:%M:%S")
        return self._send_with_retry(
            {
                "subject": "WIFI-Guard 이메일 테스트",
                "body": f"이메일 알림 경로가 정상 동작합니다.\n확인 시각: {when}",
            }
        )

    def _enqueue(self, payload: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            with self._lock:
                self._dropped_count += 1
            log.error("email notification queue is full: %s", self.email)

    def run(self) -> None:
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

    def _send_once(self, payload: dict[str, Any]) -> None:
        if not self.smtp_host:
            raise RuntimeError("SMTP_HOST is not configured")
        message = EmailMessage()
        message["Subject"] = payload["subject"]
        message["From"] = self.smtp_from
        message["To"] = self.email
        message.set_content(payload["body"])
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=5.0) as client:
            client.ehlo()
            if self.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self.smtp_username:
                client.login(self.smtp_username, self.smtp_password)
            client.send_message(message)

    def _send_with_retry(self, payload: dict[str, Any]) -> bool:
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                self._send_once(payload)
                with self._lock:
                    self._sent_count += 1
                    self._last_sent_time = time.time()
                    self._last_error = None
                return True
            except (OSError, RuntimeError, smtplib.SMTPException) as error:
                last_error = f"{type(error).__name__}: {error}"
                if attempt < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_BASE_SEC * (2 ** (attempt - 1)))
        with self._lock:
            self._failed_count += 1
            self._last_error = last_error
        log.error("email notification failed: recipient=%s error=%s", self.email, last_error)
        return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "channel": "email",
                "name": self.display_name,
                "target": self.email,
                "enabled": self.notify_fall_enabled,
                "sent_count": self._sent_count,
                "failed_count": self._failed_count,
                "dropped_count": self._dropped_count,
                "last_sent_time": self._last_sent_time,
                "last_error": self._last_error,
            }
