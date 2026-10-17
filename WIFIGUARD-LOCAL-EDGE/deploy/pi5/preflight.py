"""Read-only readiness check; no firmware, GPIO output or database writes."""

import fcntl
import json
import os
import platform
import sys
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from uuid import UUID


def main():
    from wifiguard_edge.transport.wgsp_source import (
        _SPI_IOC_RD_BITS_PER_WORD,
        _SPI_IOC_RD_MODE,
    )

    from wifiguard_local_edge.runtime import find_gpio_chip

    errors = []
    model_path = Path("/proc/device-tree/model")
    model = (
        model_path.read_text().rstrip("\x00")
        if model_path.exists()
        else platform.platform()
    )
    if "Raspberry Pi 5" not in model or platform.machine() != "aarch64":
        errors.append("physical Pi5 with a 64-bit OS required")
    if sys.version_info[:2] != (3, 12):
        errors.append("Python 3.12 required for the pinned ARM64 profile")
    for name in ("JWT_SECRET", "LOCAL_EDGE_TOKEN"):
        value = os.getenv(name, "")
        if len(value.encode()) < 32 or "REPLACE" in value:
            errors.append(f"configure a private, random {name}")
    try:
        from wifiguard_local_edge.control import read_binding
        binding = read_binding(Path(os.getenv("PI5_STATE_DIR", "/var/lib/wifiguard-pi5")))
    except (KeyError, ValueError):
        errors.append("register device and configure LOCAL_DEVICE_ID/LOCAL_TENANT_ID")
    if os.getenv("MQTT_HOST") or os.getenv("KAFKA_BOOTSTRAP"):
        errors.append("Pi5 local profile requires MQTT/Kafka connection settings empty")
    buffer_path = Path("/sys/module/spidev/parameters/bufsiz")
    if not buffer_path.exists() or int(buffer_path.read_text()) < 4608:
        errors.append("set spidev.bufsiz=8192 and reboot before Batch8")
    try:
        gpio_chip = find_gpio_chip()
        with open("/dev/spidev0.0", "rb", buffering=0) as spi:
            mode, bits = bytearray(1), bytearray(1)
            fcntl.ioctl(spi.fileno(), _SPI_IOC_RD_MODE, mode, True)
            fcntl.ioctl(spi.fileno(), _SPI_IOC_RD_BITS_PER_WORD, bits, True)
            if mode[0] != 0 or bits[0] != 8:
                errors.append("SPI must be mode 0, 8 bits; configure with sender OFF")
    except Exception as exc:  # noqa: BLE001 - readiness diagnostics, not silent success
        gpio_chip = None
        errors.append(f"SPI/GPIO unavailable: {type(exc).__name__}")
    try:
        with build_opener(ProxyHandler({})).open(
            "http://127.0.0.1:8000/health", timeout=5
        ) as response:
            health = json.load(response)
        if (
            health["status"] != "ok"
            or health["ingest"]["consumer"]["mode"] != "local-http"
        ):
            errors.append("start migrated API with both DBs and local ingestion first")
    except Exception:  # noqa: BLE001 - all API failures must fail readiness
        errors.append("local API/DB health check failed")
    try:
        import psycopg

        with psycopg.connect(os.environ["PG_DSN"], connect_timeout=3) as db:
            row = db.execute(
                "SELECT facility_id,owner_user_id FROM devices WHERE id=%s",
                (binding["device_id"],),
            ).fetchone()
        tenant = str(row[0]) if row and row[0] else f"home-{row[1]}" if row else None
        if tenant != binding["tenant_id"]:
            errors.append("local device is not registered for the configured tenant")
    except Exception:  # noqa: BLE001 - never print private connection credentials
        errors.append("local device registration check failed")
    print(
        json.dumps(
            {
                "ready": not errors,
                "hardware": model,
                "gpio_chip": gpio_chip,
                "errors": errors,
            }
        )
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
