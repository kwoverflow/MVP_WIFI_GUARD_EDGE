"""Exercise the actual runtime loop without a device, DB or external alerts."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from wifiguard_local_edge import runtime
from wifiguard_local_edge.pi5 import MeasuredPresence
from wifiguard_local_edge.presence_gate import PresenceInferenceGate


@pytest.mark.parametrize("failure", ["exception", "no_data", "sensor_reset"])
def test_presence_failure_discards_last_success(monkeypatch, failure):
    from wifiguard_edge.presence import PresenceConfig, PresenceDetector
    from wifiguard_edge.presence_loop import PresenceLoop

    stats = {"device_resets": 0, "frame_age_seconds": 0.02}
    ring = SimpleNamespace(device_resets=0, stats=lambda: dict(stats))
    presence = MeasuredPresence(ring, PresenceConfig())

    def success(loop):
        status = PresenceDetector().update(1, 100, 0)
        with loop._lock:
            loop._last, loop._last_error = status, None

    monkeypatch.setattr(PresenceLoop, "_tick", success)
    presence._tick()
    before = presence.gate_snapshot()
    assert before["state"] == "present"

    def fail(loop):
        if failure == "exception":
            raise ValueError("bad presence input")
        if failure == "no_data":
            loop._record_skip("no data")
        else:
            success(loop)
            stats["device_resets"] += 1

    monkeypatch.setattr(PresenceLoop, "_tick", fail)
    if failure == "exception":
        with pytest.raises(ValueError):
            presence._tick()
    else:
        presence._tick()
    after = presence.gate_snapshot()
    assert after["state"] is None and after["revision"] > before["revision"]
    assert after["last_error"] is not None
    assert presence.presence_payload()["state"] is None


@pytest.mark.parametrize("carrier_mode", ["training-fixed", "q-top30"])
def test_runtime_pauses_and_resumes_with_fresh_trigger_history(monkeypatch, tmp_path, carrier_mode):
    from csi_fall_segmentation import edge_features, postprocess

    from wifiguard_local_edge import budget, onnx_engine, pi5, replay
    from wifiguard_local_edge.metrics import distribution

    clock = SimpleNamespace(now=0.0)
    schedule = [
        "absent",
        "absent",
        "present",
        "present",
        "unknown",
        "present",
        "present",
        "disconnected",
        "present",
        "present",
        "absent",
    ]
    calls, resets, enqueued = [], [], []

    class Event:
        def is_set(self):
            return False

        def wait(self, delay):
            clock.now += delay
            return False

    class Ring:
        device_resets = 0

        def stats(self):
            return {"frame_age_seconds": 0.02, "device_resets": 0}

        def get_model_window(self, seconds, selection_mode="training-fixed"):
            assert schedule[int(clock.now)] == "present"
            width = 245 if selection_mode == "q-top30" else 30
            return np.arange(960) / 320 + clock.now, np.full((960, width), 20)

    class Integration:
        def __init__(self, args):
            self.buffer = Ring()
            self.gate = PresenceInferenceGate(linger_after_absent_seconds=0)

        def start(self, source):
            pass

        def inference_gate(self):
            current = schedule[int(clock.now)]
            return self.gate.evaluate(
                {
                    "state": None if current == "unknown" else current,
                    "last_error": None,
                    "completed_at": clock.now,
                    "epoch": 0,
                    "revision": sum(
                        schedule[i] != schedule[i - 1]
                        for i in range(1, int(clock.now) + 1)
                    ),
                },
                self.buffer.stats(),
                source_running=current != "disconnected",
                now=clock.now,
            )

        def require_gate(self, lease):
            self.gate.require(lease, self.inference_gate())

        def inference(self, probability, decision, engine, stages, age, *, lease):
            self.require_gate(lease)
            enqueued.append((clock.now, decision["b_trigger"]))

        def status(self):
            return {
                "inference_gate": self.gate.status(),
                "presence_metrics": {
                    "processing_ms": distribution([1]),
                    "deadline_misses": 0,
                },
                "presence": {"last_error": None},
                "feed_error": None,
                "relay": {"sent": 1, "durable_pending": 0, "overflow": 0},
            }

        def stop(self):
            return self.status()

    class Source:
        running = True
        ident = None

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return True

        def status(self):
            return {}

        def stop(self):
            pass

        def join(self, *args):
            pass

    class Builder:
        def __init__(self):
            self.last_timings_ms = {}

        def warmup(self):
            pass

        def build(self, amplitude, fs):
            calls.append(clock.now)
            return SimpleNamespace(s3=[0], acf=[0])

    class Engine:
        window_seconds, model_version = 3.0, "unchanged-model"

        def __init__(self, *args, **kwargs):
            pass

        def warmup(self):
            pass

        def predict(self, *args):
            assert schedule[int(clock.now)] == "present"
            return 0.99

    class Trigger:
        def __init__(self):
            self.count = 0

        def reset(self):
            resets.append(clock.now)
            self.count = 0

        def update(self, probability):
            self.count += 1
            return {"b_trigger": self.count >= 2}

    monkeypatch.setattr(
        runtime,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            perf_counter=lambda: clock.now,
            time=lambda: clock.now,
        ),
    )
    monkeypatch.setattr(runtime, "threading", SimpleNamespace(Event=Event))
    monkeypatch.setattr(runtime.signal, "signal", lambda *args: None)
    monkeypatch.setattr(runtime, "configure_cpu", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime,
        "ResourceSampler",
        lambda: SimpleNamespace(
            start=lambda: None, stop=lambda: None, snapshot=dict
        ),
    )
    monkeypatch.setattr(budget, "apply_budget", lambda *args: {})
    monkeypatch.setattr(pi5, "Pi5Integration", Integration)
    monkeypatch.setattr(replay, "LocalReplaySource", Source)
    monkeypatch.setattr(edge_features, "CpuEdgeFeatureBuilder", Builder)
    monkeypatch.setattr(postprocess, "ABTriggerState", Trigger)
    monkeypatch.setattr(onnx_engine, "OnnxSegmentationEngine", Engine)
    runtime.main(
        [
            "--model-carrier-selection", carrier_mode,
            "--backend",
            "onnx",
            "--model",
            "unchanged.onnx",
            "--transport",
            "replay",
            "--stride-seconds",
            "1",
            "--duration",
            "10",
            "--backend-url",
            "http://localhost",
            "--backend-token",
            "x" * 48,
            "--device-id",
            "test",
            "--tenant-id",
            "test",
            "--output-dir",
            str(tmp_path),
        ]
    )
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert calls == [2, 3, 5, 6, 8, 9]
    assert enqueued == [
        (2, False),
        (3, True),
        (5, False),
        (6, True),
        (8, False),
        (9, True),
    ]
    assert summary["counters"]["inferences"] == 6
    assert summary["counters"]["alarms"] == 3
    assert summary["counters"]["presence_gated_ticks"] == 5
    assert summary["last_result"] is None and summary["inference_paused"]
    assert summary["input_current"] is False  # final shutdown, not fabricated absence
    assert summary["model_carrier_selection"]["mode"] == carrier_mode
    assert summary["model_carrier_selection"]["checkpoint_input_equivalent"] == (carrier_mode == "training-fixed")
    assert 5 in resets and 8 in resets
