"""Fail-closed execution gate, separate from the unchanged model/postprocessor."""

from __future__ import annotations

import math
import threading
from collections import Counter


class PresenceInferencePaused(Exception):
    """A presence lease expired while preparing or evaluating a model window."""


class PresenceInferenceGate:
    def __init__(self, max_presence_age_seconds=0.75, linger_after_absent_seconds=10.0):
        if not math.isfinite(linger_after_absent_seconds) or linger_after_absent_seconds < 0:
            raise ValueError("invalid absence grace")
        self.max_presence_age_seconds = max_presence_age_seconds
        self.linger_after_absent_seconds = linger_after_absent_seconds
        self._had_present = False
        self._absent_until = None
        self._lock = threading.Lock()
        self._identity = None
        self._generation = 0
        self._current = {"open": False, "reason": "presence_unknown", "generation": 0}
        self._skips = Counter()
        self._suppressed = 0

    def evaluate(self, snapshot, stats, *, source_running, now, calibration_phase=None):
        age = now - snapshot["completed_at"]
        frame_age = stats["frame_age_seconds"]
        if calibration_phase not in (None, "done"):
            reason = f"startup_calibration:{calibration_phase}"
        elif not source_running:
            reason = "sensor_disconnected"
        elif not math.isfinite(frame_age) or not 0 <= frame_age <= 0.1:
            reason = "stale_csi"
        elif snapshot["epoch"] != stats["device_resets"]:
            reason = "sensor_reset"
        elif snapshot["last_error"] is not None:
            reason = "presence_error"
        elif snapshot["state"] is None:
            reason = "presence_unknown"
        elif not math.isfinite(age) or not 0 <= age <= self.max_presence_age_seconds:
            reason = "stale_presence"
        elif snapshot["state"] == "absent":
            reason = "absent"
        elif snapshot["state"] == "present":
            reason = "present"
        else:
            reason = "presence_unknown"
        # The presence revision catches an absent -> present interval even if
        # both transitions occur between two 1-second inference ticks.
        with self._lock:
            if reason == "present":
                self._had_present = True
                self._absent_until = None
            elif reason == "absent" and self._had_present:
                if self._absent_until is None:
                    changed = snapshot.get("state_changed_at", now)
                    if not math.isfinite(changed) or changed > now:
                        changed = now
                    self._absent_until = changed + self.linger_after_absent_seconds
                if now < self._absent_until:
                    reason = "absent_grace"
            else:
                # Faults, unknown occupancy and calibration NEVER get a grace.
                self._had_present = False
                self._absent_until = None
            identity = (reason, stats["device_resets"], snapshot["revision"], snapshot.get("config_revision"))
            # Known transitions within the grace are one continuous session.
            # Preserve AB history, but invalidate missed transitions and faults.
            continuous = (
                self._identity is not None
                and reason in ("present", "absent_grace")
                and self._identity[0] in ("present", "absent_grace")
                and identity[1] == self._identity[1]
                and identity[3] == self._identity[3]
                and 0 <= identity[2] - self._identity[2] <= 1
            )
            if identity != self._identity and not continuous:
                self._generation += 1
            self._identity = identity
            self._current = {
                "open": reason in ("present", "absent_grace"),
                "reason": reason,
                "generation": self._generation,
                "grace_remaining_seconds": max(0.0, self._absent_until - now)
                if reason == "absent_grace" else 0.0,
            }
            return dict(self._current)

    @staticmethod
    def require(lease, current):
        if (
            not lease["open"]
            or not current["open"]
            or lease["generation"] != current["generation"]
        ):
            raise PresenceInferencePaused(current["reason"])

    def record_skip(self, reason):
        with self._lock:
            self._skips[reason] += 1

    def record_suppressed(self):
        with self._lock:
            self._suppressed += 1

    def status(self):
        with self._lock:
            return {
                **self._current,
                "policy": "confirmed-present-plus-absence-grace",
                "linger_after_absent_seconds": self.linger_after_absent_seconds,
                "max_presence_age_seconds": self.max_presence_age_seconds,
                "gated_ticks": sum(self._skips.values()),
                "gated_reasons": dict(self._skips),
                "suppressed_results": self._suppressed,
            }
