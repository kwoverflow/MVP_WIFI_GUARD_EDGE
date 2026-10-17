"""One CSI buffer -> original PR presence + unchanged local fall model.

Only enabled by --backend-url. Does not change Pi3 execution or MQTT contracts.
"""

from __future__ import annotations

import threading
import time
import math
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from wifiguard_contracts import topics
from wifiguard_contracts.kafka import FeatureRecord, InferenceResult, StatusRecord
from wifiguard_edge.mqtt import codec
from wifiguard_edge.presence import PresenceConfig, PresenceDetector
from wifiguard_edge.presence_loop import PresenceLoop

from .buffer import AmplitudeBuffer
from .metrics import distribution
from .presence_gate import PresenceInferenceGate
from .relay import LocalRelay


class MeasuredPresence(PresenceLoop):
    def __init__(self, buffer, config, *, stride_sec=0.5, wander_stride_sec=None):
        if stride_sec not in (0.25, 0.5):
            raise ValueError("presence stride must be 0.25 or 0.5 seconds")
        # Only the Pi5 opt-in loop changes cadence. Keep the original signal
        # windows, calibration and Pi3 PresenceLoop default unchanged.
        super().__init__(buffer, config, stride_sec=stride_sec)
        # None preserves the previous implementation for regression/baselines.
        self.wander_stride_sec = stride_sec if wander_stride_sec is None else wander_stride_sec
        if self.wander_stride_sec not in (stride_sec, 1.0):
            raise ValueError("wander stride must match presence stride or be 1 second")
        self._wander_cached = None
        self._wander_completed_at = float("-inf")
        self._wander_signature = None
        self._wander_next_due = None
        self._wander_calls = self._wander_cache_hits = 0
        self._wander_timings = []
        self._reset_count = buffer.device_resets
        self.timings = []
        self.deadline_misses = 0
        self._completed_at = float("-inf")
        self._presence_epoch = buffer.device_resets
        self._presence_revision = 0
        self._presence_identity = None
        self._state_changed_at = float("-inf")
        self._config_revision = None
        self._config_lock = threading.RLock()

    def apply_config(self, config, revision):
        with self._config_lock:
            self.config = config
            self.detector = PresenceDetector(
                mv_threshold=config.presence_mv_threshold, wander_baseline=config.wander_baseline,
                wander_ratio_threshold=config.wander_ratio_threshold,
                wander_min_duration_s=config.wander_min_duration_s, presence_timeout_s=config.presence_timeout_s,
            )
            self._config_revision = revision
            self._invalidate("runtime config changed; waiting for fresh presence")

    def _invalidate(self, reason):
        self._wander_cached = None
        self._wander_completed_at = float("-inf")
        self._wander_signature = None
        self._wander_next_due = None
        with self._lock:
            if self._last is not None or self._presence_identity is not None:
                self._presence_revision += 1
            self._last = None
            self._completed_at = float("-inf")
            self._presence_identity = None
        self._record_skip(reason)

    def _compute_wander_current(self, cfg):
        now = time.monotonic()
        signature = tuple(asdict(cfg).items())
        # The reference cadence always computes, even with scheduler jitter.
        # Split cadence uses an anchored deadline, not completion+period, so a
        # tick a millisecond early cannot accidentally turn 1 Hz into 2/3 Hz.
        split = self.wander_stride_sec > self.stride_sec
        tolerance = min(.025, self.stride_sec * .05)
        if (split and self._wander_cached is not None
                and signature == self._wander_signature
                and self._wander_next_due is not None
                and now < self._wander_next_due - tolerance
                and 0 <= now - self._wander_completed_at < self.wander_stride_sec):
            self._wander_cache_hits += 1
            return self._wander_cached
        # A cached PSD is held for at most one configured period. It is not
        # shared with motion/model branches, whose filters and windows differ.
        started = time.perf_counter()
        value = super()._compute_wander_current(cfg)
        self._wander_timings.append((time.perf_counter() - started) * 1000)
        del self._wander_timings[:-4096]
        self._wander_calls += 1
        self._wander_cached = value
        self._wander_completed_at = now
        if self._wander_next_due is None or signature != self._wander_signature:
            self._wander_next_due = now + self.wander_stride_sec
        else:
            self._wander_next_due += self.wander_stride_sec
            if self._wander_next_due <= now:
                missed = math.floor((now - self._wander_next_due) / self.wander_stride_sec) + 1
                self._wander_next_due += missed * self.wander_stride_sec
        self._wander_signature = signature
        return value

    def gate_snapshot(self):
        # One atomic snapshot; never combine status from two different ticks.
        with self._lock:
            return {
                "state": self._presence_identity[0]
                if self._presence_identity
                else None,
                "last_error": self._last_error,
                "completed_at": self._completed_at,
                "epoch": self._presence_epoch,
                "revision": self._presence_revision,
                "state_changed_at": self._state_changed_at,
                "config_revision": self._config_revision,
            }

    def _tick(self):
        with self._config_lock:
            return self._tick_locked()

    def _tick_locked(self):
        started = time.perf_counter()
        stats = self.ring.stats()
        if (
            stats["device_resets"] != self._reset_count
            or stats["frame_age_seconds"] > 0.1
        ):
            self.detector = PresenceDetector(
                mv_threshold=self.config.presence_mv_threshold,
                wander_baseline=self.config.wander_baseline,
                wander_ratio_threshold=self.config.wander_ratio_threshold,
                wander_min_duration_s=self.config.wander_min_duration_s,
                presence_timeout_s=self.config.presence_timeout_s,
            )
            self._invalidate("sensor reset or stale CSI; presence unknown")
            self._reset_count = stats["device_resets"]
        if stats["frame_age_seconds"] > 0.1:
            return
        try:
            super()._tick()
        except Exception as exc:
            self._invalidate(
                f"presence calculation failed: {type(exc).__name__}: {exc}"
            )
            raise
        latest = self.ring.stats()
        if (
            latest["device_resets"] != stats["device_resets"]
            or latest["frame_age_seconds"] > 0.1
        ):
            self._invalidate("CSI changed during presence calculation")
            return
        duration = (time.perf_counter() - started) * 1000
        with self._lock:
            if self._last_error is not None or self._last is None:
                if self._presence_identity is not None:
                    self._presence_revision += 1
                self._last = None
                self._completed_at = float("-inf")
                self._presence_identity = None
            else:
                identity = (self._last.state.value, latest["device_resets"])
                if identity != self._presence_identity:
                    self._presence_revision += 1
                    self._state_changed_at = time.monotonic()
                self._presence_identity = identity
                self._presence_epoch = latest["device_resets"]
                self._completed_at = time.monotonic()
            self.timings.append(duration)
            del self.timings[:-4096]
            self.deadline_misses += int(duration > self.stride_sec * 1000)

    def metrics(self):
        with self._lock:
            return {
                "stride_seconds": self.stride_sec,
                "target_hz": 1 / self.stride_sec,
                "processing_ms": distribution(self.timings),
                "deadline_misses": self.deadline_misses,
                "wander_stride_seconds": self.wander_stride_sec,
                "wander_compute_calls": self._wander_calls,
                "wander_cache_hits": self._wander_cache_hits,
                "wander_processing_ms": distribution(self._wander_timings),
                "wander_cache_age_seconds": max(0.0, time.monotonic() - self._wander_completed_at)
                if self._wander_cached is not None else None,
            }


class Pi5Integration:
    def __init__(self, args):
        self.device_id, self.tenant_id = UUID(args.device_id), args.tenant_id
        self.relay = LocalRelay(
            args.backend_url, args.backend_token, args.output_dir / "outbox.sqlite3"
        )
        config = PresenceConfig()
        from wifiguard_edge.config import CalibrationConfig

        self.calibration_settings = CalibrationConfig()
        # Retain the PR calibration values instead of inventing new thresholds.
        if args.presence_config_dir:
            from wifiguard_edge.config import load_config

            loaded = load_config(
                args.presence_config_dir, require_device=False
            )
            config, self.calibration_settings = loaded.presence, loaded.calibration
        self.calibration_mode = getattr(args, "startup_calibration", None)
        if self.calibration_mode is None:
            self.calibration_mode = "empty-room" if getattr(args, "transport", None) == "spi" else "off"
        self.output_dir = args.output_dir
        self.calibration = None
        reserve = self.calibration_settings.baseline_window_s + 2 if self.calibration_mode == "empty-room" else 12
        self.buffer = AmplitudeBuffer(seconds=reserve, keep_all=True)
        self.presence = MeasuredPresence(
            self.buffer,
            config,
            stride_sec=getattr(args, "presence_stride_seconds", 0.5),
            wander_stride_sec=getattr(args, "presence_wander_stride_seconds", 1.0),
        )
        self.gate = PresenceInferenceGate()
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._feed = threading.Thread(
            target=self._feed_loop, daemon=True, name="pi5-live-feed"
        )
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._started = time.monotonic()
        self._inferences = 0
        self.carrier_selection = getattr(args, "model_carrier_selection", "training-fixed")
        self._error = None
        self.source = None
        from .runtime_config import RuntimeConfiguration
        self.runtime_configuration = RuntimeConfiguration(self.output_dir, self.device_id, self.tenant_id)

    def apply_runtime_configuration(self, engine, trigger):
        if self.calibration and not self.calibration.ready:
            return False
        return self.runtime_configuration.apply(self.presence, engine, trigger)

    def next_seq(self):
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def start(self, source):
        self.source = source
        self.relay.start()
        if self.calibration_mode == "empty-room":
            from .startup_calibration import StartupCalibration

            self.calibration = StartupCalibration(
                self.buffer, source, self.presence.config, self.output_dir,
                settings=self.calibration_settings,
            )
            self.calibration.start()
        else:
            self.presence.start()
        self._feed.start()

    def _maybe_start_presence(self):
        if self.calibration and self.calibration.ready:
            with self._lifecycle_lock:
                if not self._stop.is_set() and self.presence.ident is None:
                    # Start with clean history and the newly saved thresholds.
                    cfg = self.presence.config
                    self.presence.detector = PresenceDetector(
                        mv_threshold=cfg.presence_mv_threshold,
                        wander_baseline=cfg.wander_baseline,
                        wander_ratio_threshold=cfg.wander_ratio_threshold,
                        wander_min_duration_s=cfg.wander_min_duration_s,
                        presence_timeout_s=cfg.presence_timeout_s,
                    )
                    self.presence.start()

    def inference_gate(self):
        return self.gate.evaluate(
            self.presence.gate_snapshot(),
            self.buffer.stats(),
            source_running=bool(self.source and self.source.running),
            now=time.monotonic(),
            calibration_phase="configuration_error" if self.runtime_configuration.error else
            self.calibration.status()["phase"] if self.calibration else None,
        )

    def require_gate(self, lease):
        self.gate.require(lease, self.inference_gate())

    def _feed_loop(self):
        last_tick, last_telemetry = -1, 0.0
        while not self._stop.is_set():
            try:
                self._maybe_start_presence()
                state = self.presence.status()
                gate = self.inference_gate()
                if state["tick_count"] != last_tick:
                    last_tick = state["tick_count"]
                    msg = codec.build_presence(
                        self.presence.presence_payload(),
                        device_id=self.device_id,
                        tenant_id=self.tenant_id,
                        seq=self.next_seq(),
                        tick_uptime_s=time.monotonic() - self._started,
                    )
                    if msg:
                        record = FeatureRecord(
                            received_at=datetime.now(UTC),
                            source_topic="local/presence",
                            device_id=self.device_id,
                            tenant_id=self.tenant_id,
                            payload=msg,
                        )
                        self.relay.offer(
                            topics.KAFKA_FEATURE_STREAM, record, key="presence"
                        )
                if time.monotonic() - last_telemetry >= 1:
                    last_telemetry = time.monotonic()
                    stats = self.buffer.stats()
                    msg = codec.build_telemetry(
                        device_id=self.device_id,
                        tenant_id=self.tenant_id,
                        seq=self.next_seq(),
                        link={
                            "connected": self.source.running
                            and stats["frame_age_seconds"] <= 0.1,
                            "frames_ok": stats["total_frames"],
                            "hz_1s": stats["hz_1s"],
                            "rssi": stats["last_rssi"],
                            "buffered_seconds": stats["buffered_seconds"],
                        },
                        transport="replay"
                        if getattr(self.source, "is_replay", False)
                        else "spi",
                        presence_loop={
                            "enabled": self.presence.is_alive(),
                            "tick_count": state["tick_count"],
                            "skip_count": state["skip_count"],
                            "last_error": state["last_error"]
                            or (
                                gate["reason"]
                                if gate["reason"] not in ("present", "absent", "absent_grace")
                                else None
                            ),
                        },
                        feature_loop={
                            "enabled": True,
                            "tick_count": self._inferences,
                            "skip_count": self.gate.status()["gated_ticks"],
                            "last_error": None,
                        },
                        gate_open=gate["open"],
                        signals_published=self._inferences,
                        signals_gated=self.gate.status()["gated_ticks"],
                        uptime_s=time.monotonic() - self._started,
                        edge_version="pi5-local-pr-v1",
                    )
                    record = StatusRecord(
                        received_at=datetime.now(UTC),
                        source_topic="local/telemetry",
                        device_id=self.device_id,
                        tenant_id=self.tenant_id,
                        payload=msg,
                    )
                    self.relay.offer(topics.KAFKA_TELEMETRY, record, key="telemetry")
                self._error = None
            except Exception as exc:  # noqa: BLE001 - report thread failures in status/gate
                self._error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(0.05)

    def inference(self, probability, decision, engine, stages, frame_age, *, lease):
        # Do not publish a result calculated across an occupancy/session change.
        self.require_gate(lease)
        now = datetime.now(UTC)
        record = InferenceResult(
            device_id=self.device_id,
            tenant_id=self.tenant_id,
            seq=self.next_seq(),
            # Model classifies the center of the 3-second source window, not now.
            ts=now - timedelta(seconds=1.5 + frame_age),
            inferred_at=now,
            proba_fall=probability,
            threshold=engine.threshold,
            postprocess="segmentation_b",
            decision=bool(decision["b_trigger"]),
            feature_ms=stages["features"],
            infer_ms=stages["inference"],
            model_version=(engine.model_version + ";input=q-top30-v1")
            if self.carrier_selection == "q-top30" else engine.model_version,
            scale_cache_hit=True,
        )
        self.relay.offer(topics.KAFKA_INFERENCE_RESULT, record, durable=True)
        self._inferences += 1

    def status(self):
        return {
            "presence": {**self.presence.status(), "enabled": self.presence.is_alive()},
            "presence_metrics": self.presence.metrics(),
            "inference_gate": self.gate.status(),
            "relay": self.relay.status(),
            "feed_error": self._error,
            "feed_alive": self._feed.is_alive(),
            "startup_calibration": self.calibration.status() if self.calibration else {"phase": "disabled"},
            "runtime_configuration": self.runtime_configuration.status(),
        }

    def stop(self):
        with self._lifecycle_lock:
            self._stop.set()
            if self.calibration:
                self.calibration.cancel()
        if self.calibration and self.calibration.ident is not None:
            self.calibration.join(5)
        if self._feed.ident is not None:
            self._feed.join(2)
        self.presence.stop()
        if self.presence.ident is not None:
            self.presence.join(5)
        # Allow the final FIFO record to ACK, but never wait indefinitely.
        deadline = time.monotonic() + 2
        while (
            self.relay.is_alive()
            and self.relay.status()["durable_pending"]
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        status = self.status()
        self.relay.stop()
        return status
