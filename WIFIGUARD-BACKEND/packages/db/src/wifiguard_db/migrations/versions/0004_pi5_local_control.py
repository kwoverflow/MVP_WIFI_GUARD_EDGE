"""SPI device metadata and real calibration-run deduplication (additive)."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_devices_connection"), "devices", type_="check")
    op.drop_constraint(op.f("ck_devices_connection_target"), "devices", type_="check")
    op.create_check_constraint(op.f("ck_devices_connection"), "devices", "connection IN ('MQTT','SERIAL','SPI')")
    op.create_check_constraint(op.f("ck_devices_connection_target"), "devices",
                               "(connection='MQTT' AND mqtt_topic IS NOT NULL) OR (connection='SERIAL' AND serial_port IS NOT NULL) OR connection='SPI'")
    op.add_column("devices", sa.Column("calibration_run_id", sa.Text(), nullable=True))


def downgrade():
    # PostgreSQL rejects downgrade while SPI rows exist; never delete user data.
    op.drop_constraint(op.f("ck_devices_connection"), "devices", type_="check")
    op.drop_constraint(op.f("ck_devices_connection_target"), "devices", type_="check")
    op.create_check_constraint(op.f("ck_devices_connection"), "devices", "connection IN ('MQTT','SERIAL')")
    op.create_check_constraint(op.f("ck_devices_connection_target"), "devices",
                               "(connection='MQTT' AND mqtt_topic IS NOT NULL) OR (connection='SERIAL' AND serial_port IS NOT NULL)")
    op.drop_column("devices", "calibration_run_id")
