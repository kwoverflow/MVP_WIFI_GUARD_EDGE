"""Add opt-in mobile installations and a durable push outbox; no existing data edits."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None


def upgrade():
    op.create_table("mobile_installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("facilities.id", ondelete="CASCADE")),
        sa.Column("fcm_token", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mobile_installations_user_id", "mobile_installations", ["user_id"])
    op.create_table("mobile_push_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mobile_installations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fall_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fall_events.id", ondelete="CASCADE")),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("displayed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.UniqueConstraint("installation_id", "fall_event_id", name="uq_mobile_push_delivery_fall"),
        sa.CheckConstraint("kind IN ('FALL','TEST')", name=op.f("ck_mobile_push_deliveries_kind")),
        sa.CheckConstraint("state IN ('PENDING','ACCEPTED','FAILED','REVOKED')", name=op.f("ck_mobile_push_deliveries_state")),
    )
    op.create_index("ix_mobile_push_deliveries_installation_id", "mobile_push_deliveries", ["installation_id"])
    op.create_index("ix_mobile_push_deliveries_next_attempt_at", "mobile_push_deliveries", ["next_attempt_at"])


def downgrade():
    op.drop_table("mobile_push_deliveries")
    op.drop_table("mobile_installations")
