"""add inference provenance and idempotency to fall events

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("fall_events", sa.Column("source_event_key", sa.Text(), nullable=True))
    op.add_column("fall_events", sa.Column("model_version", sa.Text(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_fall_events_source_event_key"),
        "fall_events",
        ["source_event_key"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_fall_events_source_event_key"), "fall_events", type_="unique")
    op.drop_column("fall_events", "model_version")
    op.drop_column("fall_events", "source_event_key")
