"""add email notification fields to recipients

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recipients", sa.Column("email", sa.Text(), nullable=True))
    op.add_column(
        "recipients",
        sa.Column("email_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("recipients", "email_enabled")
    op.drop_column("recipients", "email")
