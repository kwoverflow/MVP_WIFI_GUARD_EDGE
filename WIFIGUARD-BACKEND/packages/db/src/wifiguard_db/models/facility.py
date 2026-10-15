from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin, uuid_pk

INVITE_CODE_REGEX = r"^[A-Z]{2,3}-[0-9]{4}$"


class Facility(TimestampMixin, Base):
    """시설. invite_code 는 서버가 생성(`[A-Z]{2,3}-\\d{4}`)하고 UNIQUE.

    users.facility_id ↔ facilities.root_user_id 가 순환 FK 라서 root_user_id 쪽을 use_alter + DEFERRABLE 로 둔다
    (가입 트랜잭션에서 시설·ROOT 사용자를 한 번에 삽입, 커밋 시점에 검사).
    """

    __tablename__ = "facilities"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    invite_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    root_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_facilities_root_user_id_users",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(f"invite_code ~ '{INVITE_CODE_REGEX}'", name="invite_code_format"),
    )
