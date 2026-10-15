"""Pi5 empty-room baseline using the PR's unchanged MV/Wander calibration.

WGSP is receive-only: do NOT send the legacy UART 'train' command or claim AGC
training. Every startup needs fresh operator confirmation before its 30+30 s run.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import numpy as np
from wifiguard_edge.calibration.onboarding import _compute_baseline_thresholds
from wifiguard_edge.config import CalibrationConfig

from .metrics import atomic_json
from .calibration_confirmation import confirm_empty_room

STATUS_NAME = "startup-calibration.json"
CONFIRMATION_NAME = "empty-room-confirmation.json"
RESULT_NAME = "presence-calibration.json"


def validate_baseline(window, *, duration_seconds):
    """Reject incomplete captures before the legacy function can use its floors."""
    if window is None:
        raise ValueError("no CSI baseline captured")
    times, amp = window
    if len(times) < 10 or amp.shape != (len(times), 245):
        raise ValueError("baseline requires all 245 RAW amplitude carriers")
    if not np.isfinite(times).all() or not np.isfinite(amp).all():
        raise ValueError("baseline must be finite")
    diffs = np.diff(times)
    if np.any(diffs <= 0) or float(diffs.max()) > .02:
        raise ValueError("baseline has nonmonotonic timestamps or a CSI gap > 20 ms")
    span = float(times[-1] - times[0])
    if span < duration_seconds - .05:
        raise ValueError("incomplete baseline duration")
    rate = 1 / float(np.median(diffs))
    missing_fraction = max(0.0, 1 - (len(times) - 1) / (span * rate))
    if not 300 <= rate <= 340 or missing_fraction > .02:
        raise ValueError("baseline rate/gap quality check failed")
    return {"samples": len(times), "span_seconds": span,
            "source_rate_hz": rate, "missing_fraction": missing_fraction}


class StartupCalibration(threading.Thread):
    def __init__(self, buffer, source, presence_config, output_dir, *, settings=None,
                 clock=time.monotonic, wait=None):
        super().__init__(daemon=True, name="pi5-startup-calibration")
        self.buffer, self.source, self.config = buffer, source, presence_config
        self.output_dir = Path(output_dir)
        self.settings = replace(settings or CalibrationConfig())
        self._clock = clock
        self._cancel = threading.Event()
        self._wait = wait or self._cancel.wait
        self._lock = threading.Lock()
        if (not all(math.isfinite(v) for v in asdict(self.settings).values())
                or self.settings.leave_wait_s < 0
                or self.settings.baseline_window_s < max(3, presence_config.wander_window_sec)
                or self.settings.poll_interval_s <= 0
                or self.settings.resume_timeout_s <= 0
                or self.settings.k_mv < 0
                or min(self.settings.mv_floor, self.settings.wander_baseline_floor) <= 0):
            raise ValueError("invalid startup calibration settings")
        self._state = {
            "run_id": str(uuid4()), "phase": "waiting_empty_room",
            "phase_started_at": time.time(), "error": None,
            "leave_wait_seconds": self.settings.leave_wait_s,
            "measurement_seconds": self.settings.baseline_window_s,
            "empty_room_confirmation": "operator assertion; not a CSI occupancy proof",
            "firmware_agc_performed": False,
            "firmware_agc_reason": "WGSP has no UART train control command",
            "presence_mv_threshold": None, "wander_baseline": None,
        }
        self._phase_started = self._clock()

    @property
    def ready(self):
        with self._lock:
            return self._state["phase"] == "done"

    def status(self):
        with self._lock:
            return {**self._state, "phase_elapsed_seconds": max(0, self._clock() - self._phase_started)}

    def cancel(self):
        self._cancel.set()

    def _phase(self, phase, **updates):
        if phase == "done":
            # Only expose ready after the completion status was saved. A disk
            # failure must not briefly open inference in another thread.
            snapshot = {**self.status(), "phase": phase, "phase_started_at": time.time(),
                        "phase_elapsed_seconds": 0, **updates}
            atomic_json(self.output_dir / STATUS_NAME, snapshot)
        with self._lock:
            self._state.update(phase=phase, phase_started_at=time.time(), **updates)
            self._phase_started = self._clock()
        snapshot = self.status()
        if phase != "done":
            atomic_json(self.output_dir / STATUS_NAME, snapshot)
        logging.getLogger("local-events").info(json.dumps({"event": "startup_calibration", **snapshot}))

    def _check_cancelled(self):
        if self._cancel.is_set():
            raise InterruptedError("startup calibration cancelled")

    def _sensor_ready(self):
        stats = self.buffer.stats()
        return bool(self.source.running and math.isfinite(stats["frame_age_seconds"])
                    and stats["frame_age_seconds"] <= .1
                    and stats["last_source_timestamp_seconds"] is not None)

    def _confirmed(self):
        try:
            value = json.loads((self.output_dir / CONFIRMATION_NAME).read_text(encoding="utf-8"))
            return (value.get("run_id") == self._state["run_id"]
                    and value.get("empty_room_confirmed") is True)
        except (OSError, ValueError, AttributeError):
            return False

    def run(self):
        original = self.config.presence_mv_threshold, self.config.wander_baseline
        completed = False
        try:
            self._phase("waiting_empty_room")
            while not self._confirmed():
                self._check_cancelled()
                self._wait(self.settings.poll_interval_s)
            self._check_cancelled()
            self._phase("leaving")
            self._wait(self.settings.leave_wait_s)
            self._check_cancelled()
            self._phase("waiting_csi")
            deadline = self._clock() + self.settings.resume_timeout_s
            while not self._sensor_ready():
                self._check_cancelled()
                if self._clock() >= deadline:
                    raise ValueError("fresh CSI did not arrive before baseline timeout")
                self._wait(self.settings.poll_interval_s)
            start = self.buffer.stats()
            self._phase("measuring")
            deadline = self._clock() + self.settings.baseline_window_s
            while self._clock() < deadline:
                self._check_cancelled()
                if not self._sensor_ready():
                    raise ValueError("CSI disconnected or stale during baseline")
                if self.buffer.stats()["device_resets"] != start["device_resets"]:
                    raise ValueError("receiver reset during baseline")
                self._wait(min(self.settings.poll_interval_s, deadline - self._clock()))
            self._check_cancelled()
            if not self._sensor_ready() or self.buffer.stats()["device_resets"] != start["device_resets"]:
                raise ValueError("CSI changed at baseline completion")
            window = self.buffer.get_window(self.settings.baseline_window_s)
            if window is not None:
                times, amp = window
                # Never include samples from the leave-room wait or a prior run.
                measured = times > start["last_source_timestamp_seconds"]
                window = times[measured], amp[measured]
            quality = validate_baseline(window, duration_seconds=self.settings.baseline_window_s)
            self._phase("computing", capture_quality=quality)
            times, amp = window
            mv, wander = _compute_baseline_thresholds(
                (times * 1e6, amp), self.config, self.settings.k_mv,
                self.settings.mv_floor, self.settings.wander_baseline_floor,
            )
            if not all(math.isfinite(value) and value > 0 for value in (mv, wander)):
                raise ValueError("calibration produced invalid thresholds")
            self._check_cancelled()
            if not self._sensor_ready() or self.buffer.stats()["device_resets"] != start["device_resets"]:
                raise ValueError("CSI changed while computing calibration")
            self._phase("saving")
            self.buffer.resize_seconds(12)
            candidate = replace(self.config, presence_mv_threshold=float(mv), wander_baseline=float(wander))
            atomic_json(self.output_dir / RESULT_NAME, {
                "schema_version": 1, "run_id": self._state["run_id"],
                "calibrated_at_unix": time.time(), "presence": asdict(candidate),
                "method": "original PR _compute_baseline_thresholds",
                "settings": asdict(self.settings), "capture_quality": quality,
                "firmware_agc_performed": False,
                "empty_room_verified_automatically": False,
                "input": "synthetic/replay" if getattr(self.source, "is_replay", False) else "SPI WGSP RAW",
            })
            self._check_cancelled()
            # Presence is not started until done. Update only the two original
            # calibration values, leaving all detection/model rules untouched.
            self.config.presence_mv_threshold, self.config.wander_baseline = float(mv), float(wander)
            self._phase("done", presence_mv_threshold=float(mv), wander_baseline=float(wander))
            completed = True
        except Exception as exc:
            self.config.presence_mv_threshold, self.config.wander_baseline = original
            phase = "cancelled" if isinstance(exc, InterruptedError) else "error"
            # Even if the status file cannot be saved, in-memory gate stays shut.
            try:
                self._phase(phase, error=f"{type(exc).__name__}: {exc}")
            except OSError:
                pass
        finally:
            if not completed:
                self.buffer.resize_seconds(12)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/var/lib/wifiguard-pi5"))
    parser.add_argument("--confirm-empty-room", action="store_true",
                        help="confirm you will leave the room for the entire baseline")
    args = parser.parse_args(argv)
    try:
        if args.confirm_empty_room:
            run_id = confirm_empty_room(args.output_dir)
            print(json.dumps({"event": "empty_room_confirmed", "run_id": run_id,
                              "instruction": "Leave now; remain outside until phase=done (~60 seconds plus CSI wait/compute)."}))
        else:
            state = json.loads((args.output_dir / STATUS_NAME).read_text(encoding="utf-8"))
            state["phase_elapsed_seconds"] = max(0, time.time() - state["phase_started_at"])
            print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"Calibration control failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
