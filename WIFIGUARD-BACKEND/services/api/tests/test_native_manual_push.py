"""Native Pi5 manual-only notifications, fake transport and isolated database."""
from datetime import UTC, datetime, timedelta
import math
import time
from uuid import UUID

import pytest
from sqlalchemy import select, func
from helpers import signup_home, create_device
from test_mobile_push import registration, make_result, FakeFcm
from wifiguard_db.engine import get_session
from wifiguard_db.models import MobilePushDelivery, FallEvent
from wifiguard_ingest.fall_sink import FallEventSink
from wifiguard_notify.mobile import MobilePushWorker, enqueue, native_test_push_enabled


def allow(monkeypatch):
    monkeypatch.setenv("PC_SIMULATION_MODE", "0")
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "0")
    monkeypatch.setenv("MOBILE_NATIVE_TEST_PUSH_ENABLED", "1")
    monkeypatch.setenv("MOBILE_NATIVE_TEST_PUSH_EXPIRES_AT", str(time.time() + 8 * 3600))


def test_native_manual_mode_keeps_real_fall_detection_db_but_never_sends_fall(client, db, monkeypatch):
    actor = signup_home(client, "native-manual@example.invalid")
    device = create_device(client, actor, mqttTopic="test/native-manual")
    allow(monkeypatch)
    config = client.get("/api/v1/mobile/config", headers=actor.h).json()
    assert config["pushEnabled"] and config["testOnly"] and not config["automaticFallPushEnabled"]
    response, body = registration(client, actor)
    assert response.status_code == 200
    assert FallEventSink().record(make_result(actor, device))
    assert db.execute(select(func.count()).select_from(FallEvent)).scalar_one() == 1
    assert db.execute(select(func.count()).select_from(MobilePushDelivery)).scalar_one() == 0
    endpoint = f'/api/v1/mobile/installations/{body["installationId"]}/test'
    assert client.post(endpoint, headers=actor.h, json={"confirmed": False}).status_code == 422
    assert client.post(endpoint, headers=actor.h, json={"confirmed": True}).status_code == 202
    transport = FakeFcm()
    assert MobilePushWorker(get_session, transport).drain_one()
    assert len(transport.calls) == 1 and transport.calls[0][1]["kind"] == "TEST"


@pytest.mark.parametrize("kind", ["FALL", "TEST"])
def test_native_manual_worker_rejects_any_fall_link(client, db, monkeypatch, kind):
    actor = signup_home(client, "native-fall-block@example.invalid")
    device = create_device(client, actor, mqttTopic="test/native-block")
    allow(monkeypatch)
    response, _ = registration(client, actor)
    assert FallEventSink().record(make_result(actor, device))
    fall = db.execute(select(FallEvent)).scalar_one()
    row = enqueue(db, UUID(response.json()["id"]), kind, fall.id)
    db.commit()
    transport = FakeFcm()
    assert MobilePushWorker(get_session, transport).drain_one()
    db.refresh(row)
    assert row.last_error == "PUSH_MANUAL_TEST_ONLY" and row.attempts == 0 and not transport.calls


@pytest.mark.parametrize("expiry", ["", "nan", "inf", "0", "past", "too-long"])
def test_invalid_native_grant_fails_closed(monkeypatch, expiry):
    allow(monkeypatch)
    expiry = str(time.time() - 1) if expiry == "past" else str(time.time() + 13 * 3600) if expiry == "too-long" else expiry
    monkeypatch.setenv("MOBILE_NATIVE_TEST_PUSH_EXPIRES_AT", expiry)
    assert not native_test_push_enabled()


def test_expired_native_pending_test_never_dispatches(client, db, monkeypatch):
    actor = signup_home(client, "native-expired@example.invalid")
    allow(monkeypatch)
    _, body = registration(client, actor)
    endpoint = f'/api/v1/mobile/installations/{body["installationId"]}/test'
    assert client.post(endpoint, headers=actor.h, json={"confirmed": True}).status_code == 202
    monkeypatch.setenv("MOBILE_NATIVE_TEST_PUSH_EXPIRES_AT", str(time.time() - 1))
    assert client.post(endpoint, headers=actor.h, json={"confirmed": True}).status_code == 503
    transport = FakeFcm()
    assert MobilePushWorker(get_session, transport).drain_one()
    row = db.execute(select(MobilePushDelivery)).scalar_one()
    assert row.last_error == "PUSH_DISABLED_OR_EXPIRED" and row.attempts == 0 and not transport.calls


def test_native_exception_is_not_a_pc_simulation_bypass(monkeypatch):
    allow(monkeypatch)
    monkeypatch.setenv("PC_SIMULATION_MODE", "1")
    assert not native_test_push_enabled()
