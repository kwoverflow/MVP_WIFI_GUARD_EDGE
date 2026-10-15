"""Same-Pi ingestion only; never expose a trusted record endpoint to the LAN."""

from __future__ import annotations

import hmac
import ipaddress
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult, StatusRecord

router = APIRouter(prefix="/internal/edge", include_in_schema=False)
RECORDS = {
    topics.KAFKA_FEATURE_STREAM: FeatureRecord,
    topics.KAFKA_TELEMETRY: StatusRecord,
    topics.KAFKA_INFERENCE_RESULT: InferenceResult,
}


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: Literal["csi-feature-stream", "csi-telemetry", "csi-inference-result"]
    record: dict


@router.post("/record")
def accept_record(envelope: Envelope, request: Request):
    service = authenticate_local(request)
    try:
        record = RECORDS[envelope.topic].model_validate(envelope.record)
    except ValidationError:
        raise HTTPException(422, "invalid edge record") from None
    return persist_record(service, envelope, record)


def authenticate_local(request: Request):
    service = getattr(request.app.state, "ingest", None)
    if service is None or not service.settings.local_enabled:
        raise HTTPException(404, "local ingestion disabled")
    try:
        loopback = ipaddress.ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        loopback = False
    # Do not trust forwarded headers: LAN clients via nginx are also rejected
    # by the nginx location rule in the Pi5 profile.
    supplied = request.headers.get("authorization", "")
    expected = "Bearer " + service.settings.local_edge_token
    if not loopback or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(403, "local edge authentication required")
    return service


def persist_record(service, envelope, record):
    if hasattr(record, "payload"):
        if record.payload.kind not in ("presence", "telemetry"):
            raise HTTPException(
                422, "only presence/telemetry/inference supported locally"
            )
        if (record.payload.device_id, record.payload.tenant_id) != (
            record.device_id,
            record.tenant_id,
        ):
            raise HTTPException(422, "envelope identity mismatch")
    from wifiguard_db.engine import get_session
    from wifiguard_db.models import Device

    try:
        with get_session() as db:
            device = db.get(Device, record.device_id)
            if device is None:
                raise HTTPException(
                    422, "register the local device before starting capture"
                )
            tenant = (
                str(device.facility_id)
                if device.facility_id
                else f"home-{device.owner_user_id}"
            )
            if tenant != record.tenant_id:
                raise HTTPException(403, "device tenant mismatch")
        accepted = service.consumer.handle_record(envelope.topic, record)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - fail closed, never expose a DB exception/DSN
        raise HTTPException(
            503, "local persistence unavailable; retry required"
        ) from None
    if not accepted:
        raise HTTPException(422, "record rejected")
    return {"accepted": True}
