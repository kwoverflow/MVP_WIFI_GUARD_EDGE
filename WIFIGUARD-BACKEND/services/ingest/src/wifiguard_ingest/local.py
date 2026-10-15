"""Opt-in same-host transport: retain PR contracts, sinks, state and WS hub."""

from __future__ import annotations

import threading
import logging

from .consumers import IngestConsumer

log = logging.getLogger("ingest.local")


class LocalConsumer(IngestConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._serial = threading.Lock()

    def handle_record(self, topic, record):
        # FastAPI sync handlers can run concurrently. Preserve device order.
        with self._serial:
            return super().handle_record(topic, record)

    def _on_telemetry(self, record):
        msg = record.payload
        state = self.cache.apply_telemetry(
            record.device_id, record.tenant_id, msg.link.model_dump(), msg.ts,
            input_required=True,
        )
        if self.telemetry_sink is not None:
            self.telemetry_sink.apply(record.device_id, msg.ts, online=bool(msg.link.connected))
        if not msg.gate_open:
            state = self._pause_fall(
                record.device_id, record.tenant_id,
                clear_presence=msg.presence_loop.last_error is not None,
            )
        self.hub.publish_from_thread(state)

    def _pause_fall(self, device_id, tenant_id, *, clear_presence=False):
        if self.fall_state is not None:
            self.fall_state.pause(device_id)
        return self.cache.pause_fall(device_id, tenant_id, clear_presence=clear_presence)

    def _on_presence(self, record):
        # Presence may be absent while the local 10-second grace remains open.
        # The authoritative execution gate is sent in telemetry. Closing here
        # would erase fall history before the grace-period results arrive.
        super()._on_presence(record)

    def _on_inference(self, result):
        written = False

        def persist(value):
            nonlocal written
            if self.fall_sink is None:
                return False
            duplicates = self.fall_sink.status()["duplicates"]
            written = self.fall_sink.record(value)
            # Retry after an API restart / lost HTTP response: already persisted.
            return written or self.fall_sink.status()["duplicates"] > duplicates

        update = self.fall_state.apply_durable(result, persist)
        if update is None:
            return
        fields = dict(update.fields)
        resolve_id = getattr(self.fall_sink, "event_id", None)
        if update.new_fall and resolve_id is not None:
            try:
                fields["fall_event_id"] = resolve_id(result)
            except Exception:
                # Persistence already succeeded: still show the alarm, but do
                # not pretend an unconfirmed row can be acknowledged in UI.
                log.warning("committed fall ID unavailable; acknowledgement must wait")
        state = self.cache.apply_fall(
            result.device_id, result.tenant_id, fields, result.inferred_at
        )
        self.hub.publish_from_thread(state)
        if written and self.fall_notification is not None:
            self.fall_notification.notify(result)

    def status(self):
        return {**super().status(), "mode": "local-http", "running": True}
