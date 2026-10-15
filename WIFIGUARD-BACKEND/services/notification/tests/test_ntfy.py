from __future__ import annotations

from wifiguard_notify import NtfyNotifier


def test_notifier_thread_can_stop_and_join_without_shadowing_thread_internals():
    notifier = NtfyNotifier("recipient", "unused-topic", notify_fall_enabled=False)
    notifier.start()
    notifier.stop()
    notifier.join(timeout=2)
    assert not notifier.is_alive()


def test_disabled_notification_is_not_queued():
    notifier = NtfyNotifier("recipient", "unused-topic", notify_fall_enabled=False)
    notifier.notify_fall(1, 0.9, 0.0)
    assert notifier.status()["sent_count"] == 0
    assert notifier._queue.empty()


def test_synchronous_ntfy_test_reports_success(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"ok"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    notifier = NtfyNotifier("recipient", "private-topic", server="http://ntfy.test")
    assert notifier.send_test_now() is True
    assert notifier.status()["sent_count"] == 1
