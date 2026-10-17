import json
import time
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from wifiguard_edge.calibration import onboarding
from wifiguard_edge.config import CalibrationConfig
from wifiguard_edge.presence import PresenceConfig
from wifiguard_local_edge import runtime, startup_calibration as calibration
from wifiguard_local_edge.buffer import AmplitudeBuffer
from wifiguard_local_edge.metrics import atomic_json
from wifiguard_local_edge.pi5 import Pi5Integration
from wifiguard_local_edge.presence_gate import PresenceInferenceGate, PresenceInferencePaused


def append(buffer, seq):
    buffer.append(SimpleNamespace(seq=seq, timestamp_us=round(seq * 1e6 / 320),
                                  amps=np.ones(245, dtype=np.float32) * 20,
                                  host_time=time.monotonic(), rssi=-35))


def baseline(seconds=30):
    times = np.arange(int(seconds * 320)) / 320
    amps = (20 + np.sin(times[:, None] * 2 * np.pi * .2
                       + np.arange(245)[None, :] * .04)).astype(np.float32)
    return times, amps


def test_original_calculation_is_reused_not_reimplemented():
    assert calibration._compute_baseline_thresholds is onboarding._compute_baseline_thresholds
    times, amps = baseline()
    quality = calibration.validate_baseline((times, amps), duration_seconds=30)
    assert quality["samples"] == 9600
    a = calibration._compute_baseline_thresholds((times * 1e6, amps), PresenceConfig(), 2, .3, .05)
    b = onboarding._compute_baseline_thresholds((times * 1e6, amps), PresenceConfig(), 2, .3, .05)
    np.testing.assert_array_equal(a, b)
    assert np.isfinite(a).all()


@pytest.mark.parametrize("fault", ["short", "nan", "gap", "reverse", "rate", "width", "missing"])
def test_invalid_capture_never_reaches_floor_fallback(fault):
    times, amps = baseline()
    if fault == "short":
        times, amps = times[:320], amps[:320]
    elif fault == "nan":
        amps[1, 0] = np.nan
    elif fault == "gap":
        times[100:] += .1
    elif fault == "reverse":
        times[1] = -1
    elif fault == "rate":
        times *= 2
    elif fault == "missing":
        keep = np.arange(len(times)) % 20 != 10
        times, amps = times[keep], amps[keep]
    else:
        amps = amps[:, :30]
    with pytest.raises(ValueError):
        calibration.validate_baseline((times, amps), duration_seconds=30)


class Clock:
    def __init__(self, buffer, source):
        self.now, self.seq = 0.0, 0
        self.buffer, self.source = buffer, source
        self.fault = None

    def wait(self, seconds):
        self.now += seconds
        target = round(self.now * 320)
        while self.seq <= target:
            append(self.buffer, self.seq)
            self.seq += 1
        if self.now > 40:
            if self.fault == "reset":
                self.buffer.reset()
                append(self.buffer, self.seq)
            elif self.fault == "disconnect":
                self.source.running = False


def setup(tmp_path, monkeypatch):
    buffer = AmplitudeBuffer(seconds=32, keep_all=True)
    source = SimpleNamespace(running=True, is_replay=True)
    clock = Clock(buffer, source)
    config = PresenceConfig()
    worker = calibration.StartupCalibration(buffer, source, config, tmp_path,
                                           clock=lambda: clock.now, wait=clock.wait)
    atomic_json(tmp_path / calibration.STATUS_NAME, worker.status())
    calibration.confirm_empty_room(tmp_path)
    captured = []

    def compute(window, cfg, k, mv_floor, wander_floor):
        captured.append((window, asdict(cfg), (k, mv_floor, wander_floor)))
        return 3.25, .125

    monkeypatch.setattr(calibration, "_compute_baseline_thresholds", compute)
    return worker, clock, captured


def test_30_plus_30_capture_apply_save_and_release_reserve(tmp_path, monkeypatch):
    worker, clock, captured = setup(tmp_path, monkeypatch)
    before = asdict(worker.config)
    worker.run()
    assert worker.ready and worker.status()["phase"] == "done"
    assert clock.now == pytest.approx(60)
    window_us, original, settings = captured[0]
    assert len(captured) == 1 and original == before and settings == (2, .3, .05)
    assert window_us[0][0] > 30e6 and window_us[0][-1] <= 60e6
    assert len(window_us[0]) >= 9599
    assert worker.config.presence_mv_threshold == 3.25
    assert worker.config.wander_baseline == .125
    for key in before.keys() - {"presence_mv_threshold", "wander_baseline"}:
        assert asdict(worker.config)[key] == before[key]
    saved = json.loads((tmp_path / calibration.RESULT_NAME).read_text())
    assert saved["presence"] == asdict(worker.config)
    assert saved["firmware_agc_performed"] is False
    assert saved["empty_room_verified_automatically"] is False
    assert saved["input"] == "synthetic/replay"
    assert worker.buffer.stats()["array_bytes"] < 6_000_000


@pytest.mark.parametrize("fault", ["reset", "disconnect", "nonfinite", "save", "timeout"])
def test_failure_keeps_old_values_and_closed_gate(tmp_path, monkeypatch, fault):
    worker, clock, captured = setup(tmp_path, monkeypatch)
    original = asdict(worker.config)
    saved_path = tmp_path / calibration.RESULT_NAME
    saved_path.write_text('{"previous":true}')
    if fault in ("reset", "disconnect"):
        clock.fault = fault
    elif fault == "nonfinite":
        monkeypatch.setattr(calibration, "_compute_baseline_thresholds", lambda *_: (np.nan, 1))
    elif fault == "timeout":
        worker.source.running = False
    else:
        original_save = calibration.atomic_json

        def fail_result(path, value):
            if path.name == calibration.RESULT_NAME:
                raise OSError("disk full")
            return original_save(path, value)

        monkeypatch.setattr(calibration, "atomic_json", fail_result)
    worker.run()
    assert not worker.ready and worker.status()["phase"] == "error"
    assert worker.status()["error"]
    assert asdict(worker.config) == original
    assert saved_path.read_text() == '{"previous":true}'
    assert worker.buffer.stats()["array_bytes"] < 6_000_000


def test_old_confirmation_cannot_arm_a_new_startup(tmp_path):
    source = SimpleNamespace(running=True)
    first = calibration.StartupCalibration(AmplitudeBuffer(keep_all=True), source, PresenceConfig(), tmp_path)
    atomic_json(tmp_path / calibration.STATUS_NAME, first.status())
    calibration.confirm_empty_room(tmp_path)
    second = calibration.StartupCalibration(AmplitudeBuffer(keep_all=True), source, PresenceConfig(), tmp_path)
    assert first._confirmed() and not second._confirmed()
    second.start()
    time.sleep(.1)
    assert second.status()["phase"] == "waiting_empty_room"
    second.cancel()
    second.join(2)
    assert not second.is_alive() and second.status()["phase"] == "cancelled"
    assert not (tmp_path / calibration.RESULT_NAME).exists()


def test_status_write_failure_is_fail_closed(tmp_path, monkeypatch):
    worker, _, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(calibration, "atomic_json", lambda *_: (_ for _ in ()).throw(OSError("read-only")))
    worker.run()
    assert not worker.ready and worker.status()["phase"] == "error"


def test_buffer_resize_preserves_last_windows_stream_epoch_and_sequence():
    buffer = AmplitudeBuffer(seconds=32, keep_all=True)
    for seq in range(10000):
        append(buffer, seq)
    before_window, before = buffer.get_window(10), buffer.stats()
    buffer.resize_seconds(12)
    np.testing.assert_array_equal(before_window[0], buffer.get_window(10)[0])
    np.testing.assert_array_equal(before_window[1], buffer.get_window(10)[1])
    append(buffer, 10000)
    assert buffer.stats()["device_resets"] == before["device_resets"]
    assert buffer.stats()["sequence_gaps"] == 0
    assert buffer.stats()["total_frames"] == 10001


@pytest.mark.parametrize("phase", ["waiting_empty_room", "leaving", "waiting_csi", "measuring", "computing", "saving", "error", "cancelled"])
def test_calibration_blocks_even_fresh_present_snapshot(phase):
    gate = PresenceInferenceGate()
    snapshot = {"state": "present", "completed_at": 10, "epoch": 0, "revision": 1, "last_error": None}
    stats = {"frame_age_seconds": .01, "device_resets": 0}
    good = gate.evaluate(snapshot, stats, source_running=True, now=10, calibration_phase="done")
    blocked = gate.evaluate(snapshot, stats, source_running=True, now=10, calibration_phase=phase)
    assert good["open"] and not blocked["open"]
    assert blocked["reason"] == "startup_calibration:" + phase
    with pytest.raises(PresenceInferencePaused):
        gate.require(good, blocked)


def test_pi5_defers_presence_and_model_until_calibration_done(tmp_path, monkeypatch):
    args = SimpleNamespace(device_id="00000000-0000-0000-0000-000000000001", tenant_id="home-test",
                           backend_url="http://127.0.0.1:8000", backend_token="x" * 48,
                           presence_config_dir=None, output_dir=tmp_path, transport="spi")
    integration = Pi5Integration(args)
    source = SimpleNamespace(running=True)
    # Avoid HTTP/device work; test the real integration's startup and gate wiring.
    monkeypatch.setattr(integration.relay, "start", lambda: None)
    monkeypatch.setattr(integration._feed, "start", lambda: None)
    integration.start(source)
    try:
        assert integration.presence.ident is None
        assert integration.inference_gate()["reason"] == "startup_calibration:waiting_empty_room"
        forbidden = SimpleNamespace(build=lambda *_: pytest.fail("features ran before calibration"))
        with pytest.raises(PresenceInferencePaused):
            runtime.predict_window(None, None, forbidden, None, tick=0, preprocessed=0,
                                   frame_age=0, integration=integration, lease=integration.inference_gate())
        assert integration.status()["relay"]["durable_pending"] == 0
    finally:
        integration.stop()
    assert not integration.calibration.is_alive()


def test_cli_requires_integrated_presence_and_preserves_replay_defaults(monkeypatch):
    captured = []
    monkeypatch.setattr(runtime, "run", lambda args: captured.append(args) or 0)
    base = ["--backend", "onnx", "--model", "test.onnx"]
    integrated = ["--backend-url", "http://127.0.0.1:8000", "--backend-token", "x" * 48,
                  "--device-id", "00000000-0000-0000-0000-000000000001", "--tenant-id", "home-test"]
    runtime.main(base + integrated)
    assert captured[-1].startup_calibration == "empty-room"
    assert captured[-1].stride_seconds == .5 and captured[-1].threads == 1
    runtime.main(base + integrated + ["--transport", "replay"])
    assert captured[-1].startup_calibration == "off"
    with pytest.raises(SystemExit):
        runtime.main(base + ["--startup-calibration", "empty-room"])


def test_confirmation_helper_does_not_accept_terminal_state(tmp_path):
    atomic_json(tmp_path / calibration.STATUS_NAME, {"phase": "done", "run_id": "old"})
    with pytest.raises(ValueError):
        calibration.confirm_empty_room(tmp_path)


def test_success_starts_fresh_presence_with_calibrated_values(tmp_path):
    args = SimpleNamespace(device_id="00000000-0000-0000-0000-000000000001", tenant_id="home-test",
                           backend_url="http://127.0.0.1:8000", backend_token="x" * 48,
                           presence_config_dir=None, output_dir=tmp_path, transport="spi")
    integration = Pi5Integration(args)
    integration.presence.config.presence_mv_threshold = 3.25
    integration.presence.config.wander_baseline = .125
    integration.calibration = SimpleNamespace(ready=False)
    integration._maybe_start_presence()
    assert integration.presence.ident is None
    integration.calibration.ready = True
    integration._maybe_start_presence()
    try:
        assert integration.presence.is_alive()
        identity = integration.presence.ident
        integration._maybe_start_presence()
        assert integration.presence.ident == identity
        assert integration.presence.detector.mv_threshold == 3.25
        assert integration.presence.detector.wander_baseline == .125
    finally:
        integration.presence.stop()
        integration.presence.join(2)
        integration.relay.stop()


def test_custom_calibration_settings_remain_legacy_config():
    settings = CalibrationConfig()
    assert settings.leave_wait_s == settings.baseline_window_s == 30
    assert settings.k_mv == 2 and settings.mv_floor == .3 and settings.wander_baseline_floor == .05
