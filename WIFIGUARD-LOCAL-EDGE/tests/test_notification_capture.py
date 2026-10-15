from wifiguard_local_edge.notification_capture import NotificationCapture
from wifiguard_notify import EmailNotifier, NtfyNotifier


def test_original_adapters_execute_against_loopback_only():
    capture = NotificationCapture()
    try:
        email = EmailNotifier("test", "validation@example.invalid", smtp_host="127.0.0.1",
                              smtp_port=capture.smtp.server_address[1], smtp_starttls=False,
                              smtp_username="", smtp_password="", smtp_from="local@example.invalid")
        push = NtfyNotifier("test", "validation-only", server=f"http://127.0.0.1:{capture.push.server_address[1]}")
        assert email.send_test_now()
        assert push.send_test_now()
        assert capture.status() == {"email_captured": 1, "push_http_captured": 1,
                                    "real_email_or_phone_delivery": False}
    finally:
        capture.stop()
