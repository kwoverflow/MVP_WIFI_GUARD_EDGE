from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin, uuid_fk, uuid_pk

SERVICES = ("HOME", "FACILITY")
ROLES = ("ROOT", "MEMBER", "USER")


class User(TimestampMixin, Base):
    """계정. service=HOME → role=USER·facility_id NULL / service=FACILITY → role∈{ROOT,MEMBER}·facility_id 필수 (ERD §2.1)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)  # argon2id
    name: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    facility_id: Mapped[uuid.UUID | None] = uuid_fk("facilities.id", ondelete="CASCADE")
    onboarded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        Index("ux_users_email_lower", text("lower(email)"), unique=True),
        CheckConstraint("service IN ('HOME','FACILITY')", name="service"),
        CheckConstraint("role IN ('ROOT','MEMBER','USER')", name="role"),
        CheckConstraint(
            "(service = 'HOME' AND role = 'USER' AND facility_id IS NULL)"
            " OR (service = 'FACILITY' AND role IN ('ROOT','MEMBER') AND facility_id IS NOT NULL)",
            name="service_role",
        ),
    )
