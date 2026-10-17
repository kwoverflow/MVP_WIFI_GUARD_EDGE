from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, ScopeMixin, TimestampMixin, scope_xor, uuid_pk

RECIPIENT_ROLES = ("FAMILY", "CAREGIVER", "ADMIN")  # UI 라벨: 가족 / 요양사 / 관리자


class Recipient(TimestampMixin, ScopeMixin, Base):
    """알림 수신자. 채널: phone(SMS/ARS) + ntfy_server/ntfy_topic(Push).

    레거시 `/notify/recipients`(NtfyRecipient) 와 목업 Recipient 를 한 테이블로 통합했다 (REVIEW §6 채택안).
    resident_id NULL = 공용 수신자(FACILITY 전체 낙상 알림 / HOME 전체). 발송 카운터는 알림 서비스 단계에서.
    """

    __tablename__ = "recipients"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    sms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    push: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    ars: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    ntfy_server: Mapped[str | None] = mapped_column(Text, nullable=True)
    ntfy_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    resident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("residents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    __table_args__ = (
        scope_xor(),
        CheckConstraint("role IN ('FAMILY','CAREGIVER','ADMIN')", name="role"),
    )
