from __future__ import annotations

from wifiguard_notify import EmailNotifier


def test_email_notifier_sends_test_message(monkeypatch):
    sent = []

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("mail.test", 1025, 5.0)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def ehlo(self): ...

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("smtplib.SMTP", FakeSmtp)
    notifier = EmailNotifier(
        "recipient",
        "caregiver@example.com",
        smtp_host="mail.test",
        smtp_port=1025,
        smtp_starttls=False,
    )
    assert notifier.send_test_now() is True
    assert sent[0]["To"] == "caregiver@example.com"
    assert "WIFI-Guard" in sent[0]["Subject"]
    assert notifier.status()["sent_count"] == 1
