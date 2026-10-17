"""Isolated PG + actual API/sink/outbox; FCM is a fake, never an external send."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4, UUID
import time

import pytest
from sqlalchemy import select, func
from helpers import signup_home, signup_root, signup_member, invite_code, create_device
from wifiguard_contracts.kafka import InferenceResult
from wifiguard_db.engine import get_session
from wifiguard_db.models import MobileInstallation, MobilePushDelivery, FallEvent, User
from wifiguard_ingest.fall_sink import FallEventSink
from wifiguard_notify.mobile import MobilePushWorker, PushFailure, enqueue


def registration(client, actor, installation_id=None, token=None):
    body = {"installationId": str(installation_id or uuid4()), "fcmToken": token or "mock-token-" + uuid4().hex,
            "label": "시험 휴대폰", "platform": "android"}
    response = client.put("/api/v1/mobile/installations", headers=actor.h, json=body)
    return response, body


class FakeFcm:
    def __init__(self, failure=None):
        self.calls, self.failure = [], failure
    def send(self, token, data, ttl):
        self.calls.append((token, data, ttl))
        if self.failure: raise self.failure


def test_disabled_default_and_no_anonymous_registration(client):
    actor = signup_home(client, "disabled-mobile@example.invalid")
    assert client.get("/api/v1/mobile/config").status_code == 401
    assert client.get("/api/v1/mobile/config", headers=actor.h).json()["pushEnabled"] is False
    response, _ = registration(client, actor)
    assert response.status_code == 503


def test_register_update_private_listing_and_token_not_exposed(client, monkeypatch):
    actor = signup_home(client, "mobile@example.invalid")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    response, body = registration(client, actor)
    assert response.status_code == 200, response.text
    updated, updated_body = registration(client, actor, body["installationId"], "mock-token-refreshed-" + uuid4().hex)
    assert updated.status_code == 200 and updated.json()["id"] == response.json()["id"]
    result = client.get("/api/v1/mobile/installations", headers=actor.h)
    assert len(result.json()) == 1
    assert "fcmToken" not in result.text and "mock-token" not in result.text
    other = signup_home(client, "other-mobile@example.invalid")
    assert client.get("/api/v1/mobile/installations", headers=other.h).json() == []
    stolen, _ = registration(client, other, body["installationId"], "stolen-token-" + uuid4().hex)
    assert stolen.status_code == 409
    conflict, _ = registration(client, other, token=updated_body["fcmToken"])
    assert conflict.status_code == 409


def test_pc_fixture_external_guard_even_when_mobile_env_enabled(client, monkeypatch):
    actor = signup_home(client, "pc-mobile@example.invalid")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    assert client.get("/api/v1/mobile/config", headers=actor.h).json()["pushEnabled"] is False
    response, _ = registration(client, actor)
    assert response.status_code == 503


@pytest.mark.parametrize("token", ["short", "x" * 4097, "token with spaces" + "x" * 40])
def test_invalid_token_is_rejected(client, monkeypatch, token):
    actor = signup_home(client, "invalid-mobile@example.invalid")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    response, _ = registration(client, actor, token=token)
    assert response.status_code == 422


def test_push_test_explicit_confirmation_rate_limit_receipt_is_not_operator_ack(client, monkeypatch, db):
    actor = signup_home(client, "test-mobile@example.invalid")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    response, body = registration(client, actor)
    path = f'/api/v1/mobile/installations/{body["installationId"]}/test'
    assert client.post(path, headers=actor.h, json={"confirmed": False}).status_code == 422
    results = [client.post(path, headers=actor.h, json={"confirmed": True}) for _ in range(3)]
    assert all(r.status_code == 202 for r in results)
    assert client.post(path, headers=actor.h, json={"confirmed": True}).status_code == 429
    transport = FakeFcm()
    worker = MobilePushWorker(get_session, transport)
    assert worker.drain_one()
    delivery = transport.calls[0][1]
    assert delivery["kind"] == "TEST" and delivery["fallEventId"] == ""
    receipt = client.post(f'/api/v1/mobile/deliveries/{delivery["deliveryId"]}/receipt', headers=actor.h,
                          json={"installationId": body["installationId"], "displayed": True})
    assert receipt.status_code == 200 and receipt.json()["operatorAcknowledged"] is False
    other = signup_home(client, "receipt-other@example.invalid")
    assert client.post(f'/api/v1/mobile/deliveries/{delivery["deliveryId"]}/receipt', headers=other.h,
                       json={"installationId": body["installationId"]}).status_code == 404
    listed = client.get("/api/v1/mobile/deliveries", headers=actor.h).json()
    row = next(d for d in listed if d["deliveryId"] == delivery["deliveryId"])
    assert row["state"] == "ACCEPTED" and row["receivedAt"] and row["displayedAt"]
    assert client.get("/api/v1/mobile/deliveries", headers=other.h).json() == []


def test_revoke_removes_address_and_prevents_pending_delivery(client, monkeypatch, db):
    actor = signup_home(client, "revoke-mobile@example.invalid")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    response, body = registration(client, actor)
    client.post(f'/api/v1/mobile/installations/{body["installationId"]}/test', headers=actor.h, json={"confirmed": True})
    assert client.delete(f'/api/v1/mobile/installations/{body["installationId"]}', headers=actor.h).status_code == 204
    transport = FakeFcm()
    assert not MobilePushWorker(get_session, transport).drain_one()
    assert transport.calls == []
    db.expire_all()
    row = db.get(MobileInstallation, UUID(response.json()["id"]))
    assert not row.enabled and row.fcm_token == "revoked"


def make_result(actor, device):
    now = datetime.now(UTC)
    return InferenceResult(tenant_id=f'home-{actor.user["id"]}', device_id=device["id"], ts=now, inferred_at=now,
                           seq=7, proba_fall=.99, threshold=.5, postprocess="segmentation_b", decision=True, model_version="test-only-NOT-accuracy")


def test_real_sink_atomic_mobile_outbox_idempotency_scope_and_ack(client, monkeypatch, db):
    actor = signup_home(client, "e2e-mobile@example.invalid")
    other = signup_home(client, "e2e-mobile-other@example.invalid")
    device = create_device(client, actor, mqttTopic="test/mobile")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    _, body = registration(client, actor)
    registration(client, other)
    sink = FallEventSink()
    result = make_result(actor, device)
    assert sink.record(result) is True
    assert sink.record(result) is False
    assert db.execute(select(func.count()).select_from(FallEvent)).scalar_one() == 1
    assert db.execute(select(func.count()).select_from(MobilePushDelivery)).scalar_one() == 1
    transport = FakeFcm()
    assert MobilePushWorker(get_session, transport).drain_one()
    token, data, ttl = transport.calls[0]
    assert data["installationId"] == body["installationId"] and data["kind"] == "FALL" and 1 <= ttl <= 600
    assert all(term not in str(data) for term in (actor.access, actor.refresh, token, "residentName", "rawCsi"))
    fall = db.execute(select(FallEvent)).scalar_one()
    assert fall.response == "PENDING"
    assert client.post(f'/api/v1/mobile/deliveries/{data["deliveryId"]}/receipt', headers=actor.h,
                       json={"installationId": body["installationId"], "displayed": True}).status_code == 200
    db.refresh(fall)
    assert fall.response == "PENDING"
    assert client.patch(f'/api/v1/falls/{fall.id}/response', headers=actor.h, json={"response": "ACKNOWLEDGED"}).status_code == 200
    assert client.get(f'/api/v1/falls/{fall.id}', headers=other.h).status_code == 404


def test_sink_rolls_back_fall_if_outbox_fails(client, monkeypatch, db):
    actor = signup_home(client, "atomic-mobile@example.invalid")
    device = create_device(client, actor, mqttTopic="test/mobile")
    def fail(*args): raise RuntimeError("test outbox DB fault")
    monkeypatch.setattr("wifiguard_notify.mobile.enqueue_fall", fail)
    assert FallEventSink().record(make_result(actor, device)) is False
    assert db.execute(select(func.count()).select_from(FallEvent)).scalar_one() == 0


def test_synthetic_fall_never_queues_external_push(client, monkeypatch, db):
    actor = signup_home(client, "synthetic-mobile@example.invalid")
    device = create_device(client, actor, mqttTopic="test/mobile")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    registration(client, actor)
    value = make_result(actor, device).model_copy(update={"model_version": "pc-injected-alert-routing-test-NOT-model-prediction"})
    assert FallEventSink().record(value)
    assert db.execute(select(func.count()).select_from(MobilePushDelivery)).scalar_one() == 0


@pytest.mark.parametrize("failure,expected", [(PushFailure("FCM_TIMEOUT"), "PENDING"),
                                             (PushFailure("FCM_HTTP_404", permanent=True, invalidate=True), "FAILED")])
def test_retry_and_dead_token_invalidation(client, monkeypatch, db, failure, expected):
    actor = signup_home(client, "retry-mobile@example.invalid")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    response, body = registration(client, actor)
    client.post(f'/api/v1/mobile/installations/{body["installationId"]}/test', headers=actor.h, json={"confirmed": True})
    worker = MobilePushWorker(get_session, FakeFcm(failure))
    assert worker.drain_one()
    row = db.execute(select(MobilePushDelivery)).scalar_one()
    assert row.state == expected and row.attempts == 1 and row.accepted_at is None
    assert not worker.drain_one()  # Retry backoff, no hot loop.
    db.refresh(db.get(MobileInstallation, UUID(response.json()["id"])))
    if failure.invalidate:
        assert not db.get(MobileInstallation, UUID(response.json()["id"])).enabled


def test_expiry_pc_guard_and_facility_membership_revocation(client, monkeypatch, db):
    root = signup_root(client, "root-mobile@example.invalid")
    member = signup_member(client, "member-mobile@example.invalid", invite_code(client, root))
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    response, body = registration(client, member)
    rid = UUID(response.json()["id"])
    row = enqueue(db, rid, "TEST")
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    transport = FakeFcm()
    worker = MobilePushWorker(get_session, transport)
    assert worker.drain_one()
    db.refresh(row)
    assert row.last_error == "PUSH_EXPIRED"
    row2 = enqueue(db, rid, "TEST"); db.commit()
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    assert worker.drain_one()
    db.refresh(row2)
    assert row2.last_error == "PC_EXTERNAL_PUSH_BLOCKED"
    monkeypatch.delenv("PC_SIMULATION_MODE")
    row3 = enqueue(db, rid, "TEST"); db.commit()
    user = db.get(User, UUID(member.user["id"]))
    user.facility_id = UUID(signup_root(client, "different-root@example.invalid").user["facilityId"])
    db.commit()
    assert worker.drain_one()
    db.refresh(row3)
    assert row3.state == "REVOKED" and transport.calls == []


def test_facility_fall_fans_out_only_to_current_same_facility(client, monkeypatch, db):
    root = signup_root(client, "facility-mobile-root@example.invalid")
    member = signup_member(client, "facility-mobile-member@example.invalid", invite_code(client, root))
    other = signup_root(client, "facility-mobile-other@example.invalid")
    device = create_device(client, root, mqttTopic="test/mobile/facility")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    _, root_body = registration(client, root)
    _, member_body = registration(client, member)
    registration(client, other)
    result = make_result(root, device).model_copy(update={"tenant_id": root.user["facilityId"]})
    assert FallEventSink().record(result)
    assert db.execute(select(func.count()).select_from(MobilePushDelivery)).scalar_one() == 2
    transport = FakeFcm()
    worker = MobilePushWorker(get_session, transport)
    assert worker.drain_one() and worker.drain_one() and not worker.drain_one()
    assert {data["installationId"] for _, data, _ in transport.calls} == {root_body["installationId"], member_body["installationId"]}
    fall_id = transport.calls[0][1]["fallEventId"]
    assert client.get(f"/api/v1/falls/{fall_id}", headers=member.h).status_code == 200
    assert client.get(f"/api/v1/falls/{fall_id}", headers=other.h).status_code == 404


def test_retry_limit_persists_across_worker_recreation(client, monkeypatch, db):
    actor = signup_home(client, "retry-limit-mobile@example.invalid")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    _, body = registration(client, actor)
    client.post(f'/api/v1/mobile/installations/{body["installationId"]}/test', headers=actor.h, json={"confirmed": True})
    transport = FakeFcm(PushFailure("FCM_TIMEOUT"))
    for attempt in range(1, 6):
        assert MobilePushWorker(get_session, transport).drain_one()
        db.expire_all()
        row = db.execute(select(MobilePushDelivery)).scalar_one()
        assert row.attempts == attempt
        assert row.state == ("FAILED" if attempt == 5 else "PENDING")
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert not MobilePushWorker(get_session, transport).drain_one()
    assert len(transport.calls) == 5


def allow_pc_tests(monkeypatch):
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    monkeypatch.setenv("MOBILE_PC_TEST_PUSH_ENABLED", "1")
    monkeypatch.setenv("MOBILE_PC_TEST_PUSH_EXPIRES_AT", str(time.time() + 3600))
    monkeypatch.setenv("FCM_PROJECT_ID", "mock-test-project")


def test_pc_manual_tests_require_confirmation_keep_quota_and_never_enqueue_falls(client, monkeypatch, db):
    actor = signup_home(client, "pc-manual-mobile@example.invalid")
    device = create_device(client, actor, mqttTopic="test/pc-mobile")
    allow_pc_tests(monkeypatch)
    config = client.get("/api/v1/mobile/config", headers=actor.h).json()
    assert config["pushEnabled"] and config["testOnly"]
    assert not config["automaticFallPushEnabled"]
    assert config["firebaseProjectId"] == "mock-test-project"
    response, body = registration(client, actor)
    assert response.status_code == 200
    assert FallEventSink().record(make_result(actor, device))
    assert db.execute(select(func.count()).select_from(MobilePushDelivery)).scalar_one() == 0
    path = f'/api/v1/mobile/installations/{body["installationId"]}/test'
    assert client.post(path, headers=actor.h, json={"confirmed": False}).status_code == 422
    for _ in range(3):
        assert client.post(path, headers=actor.h, json={"confirmed": True}).status_code == 202
    assert client.post(path, headers=actor.h, json={"confirmed": True}).status_code == 429
    transport = FakeFcm()
    assert MobilePushWorker(get_session, transport).drain_one()
    assert transport.calls[0][1]["kind"] == "TEST" and transport.calls[0][1]["fallEventId"] == ""
    row = db.execute(select(MobilePushDelivery).where(MobilePushDelivery.id == UUID(transport.calls[0][1]["deliveryId"]))).scalar_one()
    assert row.state == "ACCEPTED"


@pytest.mark.parametrize("kind", ["FALL", "TEST"])
def test_pc_worker_blocks_any_fall_link_even_when_manual_exception_enabled(client, monkeypatch, db, kind):
    actor = signup_home(client, "pc-block-fall-mobile@example.invalid")
    device = create_device(client, actor, mqttTopic="test/pc-block-fall")
    allow_pc_tests(monkeypatch)
    response, _ = registration(client, actor)
    assert FallEventSink().record(make_result(actor, device))
    fall = db.execute(select(FallEvent)).scalar_one()
    row = enqueue(db, UUID(response.json()["id"]), kind, fall.id)
    db.commit()
    transport = FakeFcm()
    assert MobilePushWorker(get_session, transport).drain_one()
    db.refresh(row)
    assert row.state == "REVOKED" and row.last_error == "PC_EXTERNAL_PUSH_BLOCKED"
    assert row.attempts == 0 and transport.calls == []


def test_pc_exception_expiry_blocks_new_and_pending_manual_push(client, monkeypatch, db):
    actor = signup_home(client, "pc-expired-mobile@example.invalid")
    allow_pc_tests(monkeypatch)
    _, body = registration(client, actor)
    path = f'/api/v1/mobile/installations/{body["installationId"]}/test'
    assert client.post(path, headers=actor.h, json={"confirmed": True}).status_code == 202
    monkeypatch.setenv("MOBILE_PC_TEST_PUSH_EXPIRES_AT", str(time.time() - 1))
    assert client.get("/api/v1/mobile/config", headers=actor.h).json()["pushEnabled"] is False
    assert registration(client, actor)[0].status_code == 503
    assert client.post(path, headers=actor.h, json={"confirmed": True}).status_code == 503
    transport = FakeFcm()
    assert MobilePushWorker(get_session, transport).drain_one()
    row = db.execute(select(MobilePushDelivery)).scalar_one()
    assert row.state == "REVOKED" and row.attempts == 0 and transport.calls == []
