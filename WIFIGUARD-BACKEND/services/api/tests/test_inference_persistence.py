from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from wifiguard_contracts.kafka import InferenceResult
from wifiguard_db.models import FallEvent, Recipient

from helpers import create_device, create_resident, signup_home
from wifiguard_ingest.fall_sink import FallEventSink
from wifiguard_ingest.fall_notification import FallNotificationDispatcher


class FakeNotifier:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = []
        self.__class__.instances.append(self)

    def start(self): ...

    def stop(self): ...

    def join(self, timeout=None): ...

    def notify_fall(self, fall_count, probability, at):
        self.messages.append((fall_count, probability, at))

    def status(self):
        return {"id": self.kwargs["recipient_id"], "sent_count": 0}


class FakeEmailNotifier(FakeNotifier):
    instances = []


def test_confirmed_inference_is_persisted_once(client, db):
    home = signup_home(client, "inference-fall@demo.io")
    device = create_device(client, home, connection="MQTT", name="침실 센서")
    resident = create_resident(client, home, device_id=device["id"], name="홍길동", room="침실")
    now = datetime.now(UTC)
    result = InferenceResult(
        tenant_id=f"home-{home.user['id']}",
        device_id=UUID(device["id"]),
        ts=now,
        seq=42,
        proba_fall=0.91,
        threshold=0.5,
        postprocess="none",
        inferred_at=now,
        model_version="checkpoint:abc123",
    )
    sink = FallEventSink()
    assert sink.record(result) is True
    assert sink.record(result) is False

    rows = list(db.execute(select(FallEvent)).scalars())
    assert len(rows) == 1
    fall = rows[0]
    assert str(fall.resident_id) == resident["id"]
    assert fall.source == "EDGE" and fall.response == "PENDING"
    assert fall.confidence == 0.91
    assert fall.model_version == "checkpoint:abc123"
    assert sink.status()["duplicates"] == 1


def test_persisted_fall_is_routed_to_matching_push_recipient(client, db):
    FakeNotifier.instances.clear()
    home = signup_home(client, "inference-notify@demo.io")
    device = create_device(client, home, connection="MQTT", name="침실 센서")
    create_resident(client, home, device_id=device["id"], name="홍길동", room="침실")
    recipient = Recipient(
        name="보호자",
        role="FAMILY",
        push=True,
        ntfy_server="https://ntfy.example",
        ntfy_topic="private-topic",
        owner_user_id=UUID(home.user["id"]),
    )
    db.add(recipient)
    db.commit()

    now = datetime.now(UTC)
    result = InferenceResult(
        tenant_id=f"home-{home.user['id']}",
        device_id=UUID(device["id"]),
        ts=now,
        seq=99,
        proba_fall=0.93,
        threshold=0.5,
        postprocess="none",
        inferred_at=now,
        model_version="checkpoint:test",
    )
    assert FallEventSink().record(result)
    dispatcher = FallNotificationDispatcher(notifier_factory=FakeNotifier)
    assert dispatcher.notify(result) == 1
    assert len(FakeNotifier.instances) == 1
    notifier = FakeNotifier.instances[0]
    assert notifier.kwargs["topic"] == "private-topic"
    assert notifier.messages[0][0] == 1
    assert notifier.messages[0][1] == 0.93


def test_persisted_fall_is_routed_to_email_and_push(client, db):
    FakeNotifier.instances.clear()
    FakeEmailNotifier.instances.clear()
    home = signup_home(client, "inference-multichannel@demo.io")
    device = create_device(client, home, connection="MQTT", name="침실 센서")
    create_resident(client, home, device_id=device["id"], name="홍길동", room="침실")
    db.add(Recipient(
        name="보호자",
        role="FAMILY",
        email="caregiver@example.com",
        email_enabled=True,
        push=True,
        ntfy_server="https://ntfy.example",
        ntfy_topic="private-topic",
        owner_user_id=UUID(home.user["id"]),
    ))
    db.commit()
    now = datetime.now(UTC)
    result = InferenceResult(
        tenant_id=f"home-{home.user['id']}", device_id=UUID(device["id"]), ts=now,
        seq=100, proba_fall=0.94, threshold=0.5, postprocess="none",
        inferred_at=now, model_version="checkpoint:test",
    )
    assert FallEventSink().record(result)
    dispatcher = FallNotificationDispatcher(
        notifier_factory=FakeNotifier,
        email_notifier_factory=FakeEmailNotifier,
    )
    assert dispatcher.notify(result) == 2
    assert len(FakeNotifier.instances) == 1
    assert len(FakeEmailNotifier.instances) == 1
    assert FakeEmailNotifier.instances[0].kwargs["email"] == "caregiver@example.com"
