"""Alembic 왕복(downgrade base → upgrade head)과 스키마 핵심 제약 검증."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

EXPECTED_TABLES = {
    "facilities", "users", "refresh_tokens", "tenant_configs", "devices", "residents",
    "resident_devices", "recipients", "fall_events", "event_logs", "presence_samples",
    "mobile_installations", "mobile_push_deliveries",
}


def test_downgrade_upgrade_roundtrip(alembic_cfg, migrated_db, engine):
    command.downgrade(alembic_cfg, "base")
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set()
    command.upgrade(alembic_cfg, "head")
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == EXPECTED_TABLES


def test_root_user_fk_is_deferrable(engine):
    with engine.connect() as conn:
        row = conn.execute(text(
            "select condeferrable, condeferred from pg_constraint "
            "where conname = 'fk_facilities_root_user_id_users'"
        )).one()
    assert row == (True, True)


def test_scope_xor_rejects_both_and_neither(db):
    """스코프 XOR — facility_id 와 owner_user_id 가 둘 다 NULL 이거나 둘 다 채워지면 거부."""
    from wifiguard_db.models import Device

    with pytest.raises(IntegrityError):
        db.add(Device(name="x", room="r", connection="SERIAL", serial_port="COM1"))
        db.flush()
    db.rollback()


def test_email_unique_case_insensitive(db):
    from wifiguard_db.models import User

    db.add(User(id=uuid.uuid4(), email="A@x.io", password_hash="h", name="a", service="HOME", role="USER"))
    db.flush()
    with pytest.raises(IntegrityError):
        db.add(User(id=uuid.uuid4(), email="a@X.io", password_hash="h", name="b", service="HOME", role="USER"))
        db.flush()
    db.rollback()


def test_resident_single_primary_device(db):
    from wifiguard_db.models import Device, Resident, ResidentDevice, User

    owner = User(id=uuid.uuid4(), email="o@x.io", password_hash="h", name="o", service="HOME", role="USER")
    db.add(owner)
    db.flush()
    d1 = Device(name="d1", room="거실", connection="SERIAL", serial_port="COM1", owner_user_id=owner.id)
    d2 = Device(name="d2", room="침실", connection="SERIAL", serial_port="COM2", owner_user_id=owner.id)
    r = Resident(name="r", room="거실", owner_user_id=owner.id)
    db.add_all([d1, d2, r])
    db.flush()
    db.add(ResidentDevice(resident_id=r.id, device_id=d1.id, is_primary=True))
    db.flush()
    with pytest.raises(IntegrityError):
        db.add(ResidentDevice(resident_id=r.id, device_id=d2.id, is_primary=True))
        db.flush()
    db.rollback()
