"""Local acquisition -> exact features -> CPU model -> local alarm JSONL."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .metrics import (
    ResourceSampler,
    atomic_json,
    configure_cpu,
    distribution,
    host_info,
)
from .presence_gate import PresenceInferencePaused
from .diagnostics import CaptureDiagnostics, LoopDiagnostics, StreamDiagnostics
from .status_writer import LatestStatusWriter


def predict_window(
    amplitude,
    engine,
    builder,
    trigger,
    *,
    tick,
    preprocessed,
    frame_age,
    integration=None,
    lease=None,
):
    """Check occupancy before costly work and again before emitting a result."""
    if integration:
        integration.require_gate(lease)
    features = builder.build(amplitude, 320.0)
    featured = time.perf_counter()
    if integration:
        integration.require_gate(lease)
    probability = engine.predict(features.s3[0], features.acf[0])
    inferred = time.perf_counter()
    if integration:
        integration.require_gate(lease)
    decision = trigger.update(probability)
    postprocessed = time.perf_counter()
    stages = {
        "prepare": (preprocessed - tick) * 1000,
        "features": (featured - preprocessed) * 1000,
        "inference": (inferred - featured) * 1000,
        "postprocess": (postprocessed - inferred) * 1000,
        **builder.last_timings_ms,
    }
    if integration:
        integration.inference(
            probability,
            decision,
            engine,
            stages,
            frame_age + (time.perf_counter() - tick),
            lease=lease,
        )
    stages["durable_publish"] = (time.perf_counter() - postprocessed) * 1000
    return probability, decision, stages


def find_gpio_chip(explicit=None):
    import gpiod

    paths = [Path(explicit)] if explicit else sorted(Path("/dev").glob("gpiochip*"))
    for path in paths:
        try:
            with gpiod.Chip(str(path)) as chip:
                if (
                    chip.get_info().num_lines > 25
                    and chip.get_line_info(25).name == "GPIO25"
                ):
                    return str(path)
        except PermissionError:
            # Pi5 exposes non-header controllers that the service intentionally
            # cannot access. Auto-discovery must continue to the allowed RP1 chip.
            if explicit:
                raise
    raise RuntimeError(
        "GPIO25 header line was not found; inspect gpiodetect and use --gpio-chip"
    )


def spi_source(buffer, args):
    from wifiguard_edge.config import TransportConfig
    from wifiguard_edge.transport.wgsp_source import WgspBatch8Source

    bufsiz = int(Path("/sys/module/spidev/parameters/bufsiz").read_text())
    if bufsiz < 4608:
        raise RuntimeError(
            "Batch8 requires spidev.bufsiz >= 4608; configure 8192 and reboot"
        )

    class LocalSource(WgspBatch8Source):
        def __init__(self, *values, **options):
            super().__init__(*values, **options)
            self.diagnostics = StreamDiagnostics()
            self.capture_diagnostics = CaptureDiagnostics()

        def _observe_batch(self, timing):
            self.capture_diagnostics.observe(timing)

        def _append(self, frame):
            if self._last_epoch is not None and self._last_epoch != frame.stream_epoch:
                self.buffer.reset()
            super()._append(frame)
            if frame.usable_for_preprocessing:
                self.diagnostics.observe(frame, self._current_batch_timing)

        def status(self):
            return {
                **super().status(),
                "protocol_rejects": self._protocol_rejects,
                "protocol_resyncs": self._protocol_resyncs,
                "transport_errors": self._transport_errors,
                "reject_reasons": dict(self._reject_reasons),
                "diagnostics": self.diagnostics.status(),
                "capture_timing": self.capture_diagnostics.status(),
            }

    config = TransportConfig(
        kind="spi",
        ready_gpio_chip=find_gpio_chip(args.gpio_chip),
        max_speed_hz=args.spi_speed_hz,
        spi_bus=args.spi_bus,
        spi_device=args.spi_device,
    )
    return LocalSource(buffer, config)


def run(args):
    from .budget import apply_budget

    budget = apply_budget(args.cpu_affinity, args.memory_limit_mib)
    configure_cpu(args.threads, use_torch=args.backend == "torch")
    from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder
    from csi_fall_segmentation.postprocess import ABTriggerState
    from threadpoolctl import threadpool_limits

    from .buffer import AmplitudeBuffer
    from .carriers import ModelWindowPreparer
    from .replay import LocalReplaySource, RawWgspReplaySource

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("local-events")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(
        args.output_dir / "events.jsonl",
        maxBytes=4 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    sampler = ResourceSampler()
    sampler.start()
    status_writer = LatestStatusWriter(args.output_dir / "status.json")
    source = None
    integration = None
    timings, stage_times = [], {}
    counters = {
        "inferences": 0,
        "alarms": 0,
        "deadline_misses": 0,
        "skipped_deadlines": 0,
        "invalid_windows": 0,
        "stale_windows": 0,
        "trigger_resets": 0,
        "steady_invalid_windows": 0,
        "steady_stale_windows": 0,
        "presence_gated_ticks": 0,
        "presence_suppressed_results": 0,
        "presence_session_resets": 0,
        "input_wait_ticks": 0,
        "steady_input_wait_ticks": 0,
        "steady_input_gap_events": 0,
    }
    result = {}
    diagnostics = LoopDiagnostics()
    last_window_error = None
    try:
        with threadpool_limits(limits=1, user_api="blas"):
            startup = time.perf_counter()
            if args.backend == "torch":
                from csi_fall_segmentation.engine import SegmentationInferenceEngine

                engine = SegmentationInferenceEngine(args.checkpoint, device="cpu")
            else:
                from .onnx_engine import OnnxSegmentationEngine

                engine = OnnxSegmentationEngine(args.model, threads=args.threads)
            if engine.window_seconds != 3.0:
                raise ValueError(
                    "local window contract requires the reviewed 3-second checkpoint"
                )
            builder = CpuEdgeFeatureBuilder()
            builder.warmup()
            engine.warmup()
            from .interpolation import warmup as warmup_interpolation

            # Compile before opening capture; first inference must not JIT while
            # the receiver's small hardware queue is filling.
            warmup_interpolation()
            startup_seconds = time.perf_counter() - startup
            if getattr(args, "backend_url", None):
                from .pi5 import Pi5Integration

                integration = Pi5Integration(args)
            selection_mode = getattr(args, "model_carrier_selection", "training-fixed")
            preparer = ModelWindowPreparer(selection_mode)
            buffer = integration.buffer if integration else AmplitudeBuffer(keep_all=selection_mode == "q-top30")
            source = (
                spi_source(buffer, args)
                if args.transport == "spi"
                else RawWgspReplaySource(buffer, scenario_file=getattr(args, "simulation_scenario_file", None))
                if args.replay_source == "synthetic-wgsp"
                else LocalReplaySource(
                    buffer,
                    source=args.replay_source,
                )
            )
            trigger = ABTriggerState()
            previous_alarm = False
            last_timestamp = None
            last_resets = buffer.device_resets
            started = time.monotonic()
            deadline = started
            last_decision = None
            last_gate_generation = None
            last_input_gap_events = 0
            source.start()
            if integration:
                integration.start(source)
            print(
                json.dumps(
                    {
                        "event": "local_edge_started",
                        "startup_seconds": startup_seconds,
                        "stride_seconds": args.stride_seconds,
                        "model_version": engine.model_version,
                        "original_trigger_cadence": args.stride_seconds == 0.25,
                        "model_carrier_selection": selection_mode,
                        "checkpoint_input_equivalent": selection_mode == "training-fixed",
                        "host": host_info(),
                    }
                ),
                flush=True,
            )
            while not stop.is_set() and (
                args.duration is None or time.monotonic() - started < args.duration
            ):
                if not source.is_alive():
                    raise RuntimeError(
                        "CSI source stopped unexpectedly; check SPI/GPIO permission and receiver"
                    )
                if stop.wait(max(0.0, deadline - time.monotonic())):
                    break
                tick = time.perf_counter()
                if integration and hasattr(integration, "apply_runtime_configuration"):
                    integration.apply_runtime_configuration(engine, trigger)
                stats = buffer.stats()
                gate = integration.inference_gate() if integration else None
                input_state = buffer.model_window_status() if hasattr(buffer, "model_window_status") else {"ready": True, "gap_events": 0}
                if counters["inferences"]:
                    counters["steady_input_gap_events"] += max(0, input_state["gap_events"] - last_input_gap_events)
                last_input_gap_events = input_state["gap_events"]
                # No model-window copies or feature/model work while the gate is closed.
                window = None
                if (gate is None or gate["open"]) and input_state["ready"]:
                    window = (buffer.get_model_window(3.02, selection_mode)
                              if selection_mode == "q-top30"
                              else buffer.get_model_window(3.02))
                reset_trigger = False
                if buffer.device_resets != last_resets:
                    trigger.reset()
                    previous_alarm = False
                    last_timestamp = None
                    last_resets = buffer.device_resets
                    counters["trigger_resets"] += 1
                    last_decision = None
                if gate is not None and gate["generation"] != last_gate_generation:
                    logger.info(
                        json.dumps(
                            {
                                "event": "presence_inference_gate",
                                **gate,
                                "wall_time_unix": time.time(),
                            }
                        )
                    )
                    trigger.reset()
                    previous_alarm = False
                    last_timestamp = None
                    last_decision = None
                    last_gate_generation = gate["generation"]
                    counters["presence_session_resets"] += 1
                if gate is not None and not gate["open"]:
                    counters["presence_gated_ticks"] += 1
                    integration.gate.record_skip(gate["reason"])
                    reset_trigger = True
                elif not input_state["ready"]:
                    counters["input_wait_ticks"] += 1
                    if counters["inferences"]:
                        counters["steady_input_wait_ticks"] += 1
                    reset_trigger = True
                elif (
                    window is None
                    or stats["frame_age_seconds"] > 0.1
                    or (last_timestamp is not None and window[0][-1] <= last_timestamp)
                ):
                    counters["stale_windows"] += 1
                    if counters["inferences"]:
                        counters["steady_stale_windows"] += 1
                    reset_trigger = True
                else:
                    try:
                        amplitude = preparer.prepare(*window)
                    except ValueError as exc:
                        counters["invalid_windows"] += 1
                        if counters["inferences"]:
                            counters["steady_invalid_windows"] += 1
                        last_window_error = str(exc)
                        diagnostics.rejected_window(window, exc, source.status())
                        reset_trigger = True
                    else:
                        last_window_error = None
                        last_timestamp = float(window[0][-1])
                        preprocessed = time.perf_counter()
                        try:
                            probability, decision, stages = predict_window(
                                amplitude,
                                engine,
                                builder,
                                trigger,
                                tick=tick,
                                preprocessed=preprocessed,
                                frame_age=stats["frame_age_seconds"],
                                integration=integration,
                                lease=gate,
                            )
                        except PresenceInferencePaused:
                            counters["presence_suppressed_results"] += 1
                            integration.gate.record_suppressed()
                            reset_trigger = True
                        else:
                            alarm = bool(decision["b_trigger"])
                            if alarm and not previous_alarm:
                                counters["alarms"] += 1
                                logger.info(
                                    json.dumps(
                                        {
                                            "event": "fall_alarm",
                                            "probability": probability,
                                            "source_center_seconds": last_timestamp
                                            - 1.5,
                                            "wall_time_unix": time.time(),
                                            "decision": decision,
                                        }
                                    )
                                )
                            previous_alarm = alarm
                            counters["inferences"] += 1
                            # Bounded history (~one hour at 4 Hz), no raw CSI writes.
                            if len(timings) > 14400:
                                del timings[:7200]
                                for values in stage_times.values():
                                    del values[:7200]
                            for key, value in stages.items():
                                stage_times.setdefault(key, []).append(value)
                            # Include durable enqueue cost in the 1-second budget.
                            total_ms = (time.perf_counter() - tick) * 1000
                            timings.append(total_ms)
                            if total_ms > args.stride_seconds * 1000:
                                counters["deadline_misses"] += 1
                                diagnostics.record("processing_deadline_miss", total_ms=total_ms,
                                                   stages_ms=dict(stages), source=source.status())
                            last_decision = {
                                "probability": probability,
                                "decision": decision,
                                "source_center_seconds": last_timestamp - 1.5,
                                "total_processing_ms": total_ms,
                                "stages_ms": dict(stages),
                            }
                if reset_trigger:
                    trigger.reset()
                    previous_alarm = False
                    last_timestamp = None
                    last_decision = None
                elapsed = time.monotonic() - started
                result = {
                    "event": "local_edge_status",
                    "transport": args.transport,
                    "running": True,
                    "device_id": str(integration.device_id) if integration and hasattr(integration, "device_id") else None,
                    "updated_at_unix": time.time(),
                    "input_current": source.running
                    and stats["frame_age_seconds"] <= 0.1,
                    "inference_paused": bool(
                        gate is not None and (not gate["open"] or reset_trigger)
                    ),
                    "elapsed_seconds": elapsed,
                    "startup_seconds": startup_seconds,
                    "stride_seconds": args.stride_seconds,
                    "original_trigger_cadence": args.stride_seconds == 0.25,
                    "model_fixed_delay_seconds": 1.5,
                    "model_version": engine.model_version,
                    "backend": args.backend,
                    "model_threads": args.threads,
                    "model_carrier_selection": preparer.last_selection,
                    "resource_budget": budget,
                    "host": host_info(),
                    "counters": dict(counters),
                    "buffer": buffer.stats(),
                    "source": source.status(),
                    "processing_ms": distribution(timings),
                    "last_window_error": last_window_error,
                    "diagnostics": diagnostics.status(),
                    "input_window": input_state,
                    "stage_ms": {k: distribution(v) for k, v in stage_times.items()},
                    "resources": sampler.snapshot(),
                    "status_writer": status_writer.status(),
                    "last_result": last_decision,
                    "pi5_services": integration.status() if integration else None,
                }
                # Live monitoring may coalesce snapshots, never durable model
                # results. FULL-synchronous relay.offer above is still blocking.
                status_writer.publish(result)
                if args.verbose:
                    print(json.dumps(result), flush=True)
                deadline += args.stride_seconds
                now = time.monotonic()
                if deadline <= now:
                    skipped = int((now - deadline) // args.stride_seconds) + 1
                    counters["skipped_deadlines"] += skipped
                    deadline += skipped * args.stride_seconds
                    trigger.reset()
                    previous_alarm = False
                    counters["trigger_resets"] += 1
            result["counters"] = dict(counters)
            # Drain before the final synchronous stopped/failure record so an
            # older live snapshot can never overwrite the final status.
            status_writer.close()
            result["event"] = "local_edge_stopped"
            result["running"] = False
            result["input_current"] = False
            source.stop()
            source.join(2)
            if integration:
                result["pi5_services"] = integration.stop()
                integration = None
            result["source"] = source.status()
            result["resources"] = sampler.snapshot()
            timing = distribution(timings)
            result["live_timing_gate_passed"] = bool(
                counters["inferences"] >= 30
                and timing["p95"] <= args.stride_seconds * 800
                and counters["deadline_misses"] == 0
                and counters["skipped_deadlines"] == 0
                and counters["steady_invalid_windows"] == 0
                and counters["steady_stale_windows"] == 0
                and counters["steady_input_wait_ticks"] == 0
                and counters["steady_input_gap_events"] == 0
            )
            result["pi5_hardware_timing_gate_passed"] = bool(
                host_info()["pi5_hardware"] and result["live_timing_gate_passed"]
            )
            services = result.get("pi5_services")
            if services:
                pm = services["presence_metrics"]
                result["combined_local_gate_passed"] = bool(
                    result["live_timing_gate_passed"]
                    and pm["processing_ms"]["p95"] is not None
                    and pm["processing_ms"]["p95"]
                    < pm.get("stride_seconds", 0.25) * 800
                    and pm["deadline_misses"] == 0
                    and services["relay"]["sent"] > 0
                    and services["relay"]["durable_pending"] == 0
                    and services["relay"]["overflow"] == 0
                    and services["feed_error"] is None
                    and services["presence"]["last_error"] is None
                )
                result["pi5_hardware_timing_gate_passed"] &= result[
                    "combined_local_gate_passed"
                ]
            atomic_json(args.output_dir / "summary.json", result)
            atomic_json(args.output_dir / "status.json", result)
            print(json.dumps(result), flush=True)
            return (
                0
                if result.get(
                    "combined_local_gate_passed", result["live_timing_gate_passed"]
                )
                else 2
            )
    except Exception as exc:
        try:
            status_writer.close()
        except Exception:
            # Preserve the original failure and still attempt a stopped record.
            # The finalizer must release capture/relay even after a disk error.
            pass
        failure = {
            **result,
            "event": "local_edge_failed",
            "running": False,
            "error": str(exc),
            "counters": dict(counters),
        }
        atomic_json(args.output_dir / "status.json", failure)
        atomic_json(args.output_dir / "summary.json", failure)
        raise
    finally:
        try:
            status_writer.close()
        except Exception:
            logger.exception("live status writer shutdown failed")
        if integration is not None:
            integration.stop()
        if source is not None:
            source.stop()
            if source.ident is not None:
                source.join(2)
        sampler.stop()
        logger.removeHandler(handler)
        handler.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--backend", choices=("torch", "onnx"), default="torch")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--cpu-affinity", type=lambda s: [int(i) for i in s.split(",")])
    parser.add_argument("--memory-limit-mib", type=int)
    parser.add_argument("--transport", choices=("spi", "replay"), default="spi")
    parser.add_argument("--replay-source", default="synthetic")
    parser.add_argument("--simulation-scenario-file", type=Path,
                        help="PC RAW replay fixture only; never accepted for real SPI")
    parser.add_argument(
        "--stride-seconds", type=float, choices=(0.25, 0.5, 0.75, 1.0),
        help="model cadence: ONNX/Pi5 defaults to 0.5 s; legacy Torch to 0.25 s",
    )
    parser.add_argument(
        "--threads", type=int, choices=(1, 2, 3, 4),
        help="model workers: ONNX/Pi5 defaults to 1; legacy Torch to 2",
    )
    parser.add_argument("--gpio-chip")
    parser.add_argument("--spi-bus", type=int, default=0)
    parser.add_argument("--spi-device", type=int, default=0)
    parser.add_argument("--spi-speed-hz", type=int, default=6000000)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--output-dir", type=Path, default=Path("local-output"))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--backend-url", help="opt-in PR API connection, HTTP loopback only"
    )
    parser.add_argument(
        "--backend-token",
        default=os.getenv("LOCAL_EDGE_TOKEN", ""),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--device-id", default=os.getenv("LOCAL_DEVICE_ID"))
    parser.add_argument("--tenant-id", default=os.getenv("LOCAL_TENANT_ID"))
    parser.add_argument("--presence-config-dir", type=Path)
    parser.add_argument(
        "--startup-calibration", choices=("empty-room", "off"),
        help="integrated SPI defaults to empty-room: fresh confirmation, 30 s leave + 30 s baseline; replay defaults off",
    )
    parser.add_argument(
        "--presence-stride-seconds",
        type=float,
        choices=(0.25, 0.5),
        default=0.5,
        help="Pi5 integrated presence cadence; does not change model stride",
    )
    parser.add_argument(
        "--presence-wander-stride-seconds", type=float, choices=(0.25, 0.5, 1.0), default=1.0,
        help="Pi5 PSD compute period; cached only within this period",
    )
    parser.add_argument(
        "--model-carrier-selection", choices=("training-fixed", "q-top30"), default="training-fixed",
        help="q-top30 changes checkpoint inputs; keep training-fixed for the original model contract",
    )
    args = parser.parse_args(argv)
    if args.simulation_scenario_file and (args.transport != "replay" or args.replay_source != "synthetic-wgsp"):
        parser.error("simulation scenario requires synthetic-wgsp replay, not SPI")
    if args.backend_url and args.output_dir.joinpath("device-binding.json").exists():
        from .control import read_binding
        bound = read_binding(args.output_dir)
        args.device_id, args.tenant_id = bound["device_id"], bound["tenant_id"]
    if args.stride_seconds is None:
        args.stride_seconds = 0.5 if args.backend == "onnx" else 0.25
    if args.threads is None:
        args.threads = 1 if args.backend == "onnx" else 2
    if args.startup_calibration is None:
        args.startup_calibration = "empty-room" if args.backend_url and args.transport == "spi" else "off"
    if args.startup_calibration == "empty-room" and not args.backend_url:
        parser.error("startup calibration requires the integrated presence path (--backend-url)")
    if args.backend == "torch" and args.checkpoint is None:
        parser.error("torch backend requires --checkpoint")
    if args.backend == "onnx" and args.model is None:
        parser.error("ONNX backend requires --model")
    if args.duration is not None and args.duration <= 0:
        parser.error("duration must be positive")
    if args.backend_url and (
        not args.device_id or not args.tenant_id or not args.backend_token
    ):
        parser.error(
            "local backend requires LOCAL_EDGE_TOKEN, LOCAL_DEVICE_ID and LOCAL_TENANT_ID"
        )
    if args.backend_url and args.presence_wander_stride_seconds not in (args.presence_stride_seconds, 1.0):
        parser.error("PSD period must match presence period or be 1 second")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
