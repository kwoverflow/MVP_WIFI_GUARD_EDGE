"""FCM wire/error contracts only: no Google account, credentials or external send."""
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from wifiguard_notify.mobile import FcmTransport, PushFailure, _NoRedirect


class Credentials:
    def __init__(self, valid=True):
        self.token = "test-oauth-NOT-real" if valid else None
        self.refreshes = 0

    @property
    def valid(self):
        return bool(self.token)

    def refresh(self, request):
        self.refreshes += 1
        request("https://oauth2.googleapis.com/token")
        self.token = "test-refreshed-NOT-real"


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def transport(handler, valid=True):
    value = FcmTransport.__new__(FcmTransport)
    value.credentials = Credentials(valid)
    value.auth_request = lambda *args, **kwargs: kwargs
    value.endpoint = "https://fcm.googleapis.com/v1/projects/test-project/messages:send"
    value.opener = SimpleNamespace(open=handler)
    return value


def test_data_only_high_priority_ttl_bearer_and_bounded_timeout():
    seen = []
    def handle(request, timeout):
        seen.append((request, timeout))
        return Response(b'{"name":"projects/test-project/messages/mock"}')
    value = transport(handle, valid=False)
    value.send("mock-installation-token", {"deliveryId": "mock"}, 600)
    request, timeout = seen[0]
    assert timeout == 10 and request.full_url == value.endpoint
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-refreshed-NOT-real"
    message = json.loads(request.data)["message"]
    assert message == {"token": "mock-installation-token", "data": {"deliveryId": "mock"},
                       "android": {"priority": "high", "ttl": "600s"}}
    assert "notification" not in message
    assert value.credentials.refreshes == 1


@pytest.mark.parametrize("status,details,permanent,invalidate", [
    (400, [], True, False), (403, [], True, False),
    (404, [{"errorCode": "UNREGISTERED"}], True, True),
    (401, [], False, False), (429, [], False, False), (503, [], False, False),
])
def test_http_errors_are_sanitized_and_retry_flags(status, details, permanent, invalidate):
    def handle(request, timeout):
        body = json.dumps({"error": {"message": "DO-NOT-LEAK-TOKEN", "details": details}}).encode()
        raise HTTPError(request.full_url, status, "DO-NOT-LEAK-TOKEN", {}, io.BytesIO(body))
    value = transport(handle)
    with pytest.raises(PushFailure) as result:
        value.send("mock-installation-token", {}, 10)
    assert result.value.code == f"FCM_HTTP_{status}"
    assert result.value.permanent is permanent and result.value.invalidate is invalidate
    assert "DO-NOT-LEAK" not in str(result.value)
    if status == 401:
        assert not value.credentials.valid


@pytest.mark.parametrize("body", [b"not-json", b"{}"])
def test_invalid_response_never_counts_as_accepted(body):
    with pytest.raises(PushFailure):
        transport(lambda *a, **kw: Response(body)).send("mock", {}, 10)


def test_network_failure_and_auth_failure_are_sanitized():
    def failed(*args, **kwargs): raise URLError("private diagnostic")
    with pytest.raises(PushFailure, match="^FCM_TRANSPORT_ERROR$"):
        transport(failed).send("mock", {}, 10)
    value = transport(failed, valid=False)
    value.credentials.refresh = failed
    with pytest.raises(PushFailure, match="^FCM_AUTH_FAILED$"):
        value.send("mock", {}, 10)


def test_credentials_are_never_forwarded_on_redirect():
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted.invalid") is None


@pytest.mark.parametrize("expiry,allowed", [
    ("", False), ("invalid", False), ("nan", False), ("inf", False),
    ("999", False), ("1000", False), ("4601", False),
    ("1001", True), ("4600", True),
])
def test_pc_exception_is_bounded_fails_closed_and_never_enables_falls(monkeypatch, expiry, allowed):
    from wifiguard_notify import mobile
    monkeypatch.setattr(mobile.time, "time", lambda: 1000)
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    monkeypatch.setenv("MOBILE_PC_TEST_PUSH_ENABLED", "1")
    monkeypatch.setenv("MOBILE_PC_TEST_PUSH_EXPIRES_AT", expiry)
    assert mobile.pc_test_push_enabled() is allowed
    assert mobile.test_push_enabled() is allowed
    assert mobile.enabled() is False


@pytest.mark.parametrize("name", ["MOBILE_PUSH_ENABLED", "MOBILE_PC_TEST_PUSH_ENABLED"])
def test_pc_exception_requires_both_explicit_flags(monkeypatch, name):
    from wifiguard_notify import mobile
    monkeypatch.setattr(mobile.time, "time", lambda: 1000)
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    monkeypatch.setenv("MOBILE_PC_TEST_PUSH_ENABLED", "1")
    monkeypatch.setenv("MOBILE_PC_TEST_PUSH_EXPIRES_AT", "4600")
    monkeypatch.delenv(name)
    assert mobile.test_push_enabled() is False


def test_production_enable_behavior_is_unchanged(monkeypatch):
    from wifiguard_notify import mobile
    monkeypatch.delenv("PC_SIMULATION_MODE", raising=False)
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    assert mobile.enabled() is True and mobile.test_push_enabled() is True
    assert mobile.pc_test_push_enabled() is False
