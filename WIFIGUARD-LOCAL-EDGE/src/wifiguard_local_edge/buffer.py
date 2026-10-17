"""Bounded array ring: select 30 carriers once per incoming frame."""

from __future__ import annotations

import threading
import time

import numpy as np

from wifiguard_edge.features.realtime import FeatureConfig, select_subcarrier_indices


class AmplitudeBuffer:
    def __init__(self, fs_hz=320.0, seconds=5.0, *, keep_all=False):
        self.fs_hz = fs_hz
        self.capacity = int(fs_hz * seconds * 1.5)
        self.keep_all = keep_all
        self.amps = np.zeros((self.capacity, 245 if keep_all else 30), dtype=np.float32)
        self.times = np.zeros(self.capacity, dtype=np.float64)
        self._lock = threading.Lock()
        self._indices: dict[int, np.ndarray] = {}
        self._head = self._count = 0
        self.total_frames = self.device_resets = self.sequence_gaps = 0
        self._raw_ts = self._last_seq = None
        self._offset = 0
        self.last_host_time = self.last_rssi = 0.0
        self._continuous_since = None
        self.input_gap_events = 0

    def reset(self):
        with self._lock:
            self._head = self._count = 0
            self._raw_ts = self._last_seq = None
            self._offset = 0
            self._continuous_since = None
            self.device_resets += 1

    def resize_seconds(self, seconds):
        """Release the one-off calibration reserve without resetting the stream."""
        capacity = int(self.fs_hz * seconds * 1.5)
        if capacity < 2:
            raise ValueError("CSI buffer requires at least two samples")
        with self._lock:
            count = min(self._count, capacity)
            indices = (np.arange(count) + self._head - count) % self.capacity
            amps = np.zeros((capacity, self.amps.shape[1]), dtype=np.float32)
            times = np.zeros(capacity, dtype=np.float64)
            amps[:count], times[:count] = self.amps[indices], self.times[indices]
            self.amps, self.times, self.capacity = amps, times, capacity
            self._count, self._head = count, count % capacity

    def append(self, frame):
        width = len(frame.amps)
        if width not in self._indices:
            selected = select_subcarrier_indices(width, FeatureConfig())
            if len(selected) != 30:
                raise ValueError("local model requires at least 30 CSI pairs")
            self._indices[width] = selected
        if self.keep_all and width != 245:
            raise ValueError("PR presence compatibility requires all 245 CSI carriers")
        values = frame.amps if self.keep_all else frame.amps[self._indices[width]]
        if not np.isfinite(values).all():
            raise ValueError("CSI amplitudes must be finite")
        with self._lock:
            if self._last_seq is not None and frame.seq <= self._last_seq:
                self._count = self._head = self._offset = 0
                self._raw_ts = self._last_seq = None
                self.device_resets += 1
                self._continuous_since = None
            if self._raw_ts is not None and frame.timestamp_us < self._raw_ts:
                if self._raw_ts - frame.timestamp_us > (1 << 31):
                    self._offset += 1 << 32
                else:
                    self._count = self._head = self._offset = 0
                    self._last_seq = None
                    self.device_resets += 1
                    self._continuous_since = None
            if self._last_seq is not None:
                self.sequence_gaps += max(0, frame.seq - self._last_seq - 1)
            timestamp = (frame.timestamp_us + self._offset) / 1e6
            if self._count and self._continuous_since is not None:
                delta = timestamp - self.times[(self._head - 1) % self.capacity]
                if delta <= 0 or delta > .02:
                    self.input_gap_events += 1
                    self._continuous_since = timestamp
            if self._continuous_since is None:
                self._continuous_since = timestamp
            self._raw_ts, self._last_seq = frame.timestamp_us, frame.seq
            self.times[self._head] = timestamp
            self.amps[self._head] = values
            self._head = (self._head + 1) % self.capacity
            self._count = min(self._count + 1, self.capacity)
            self.total_frames += 1
            self.last_host_time, self.last_rssi = frame.host_time, frame.rssi

    def get_window(self, seconds):
        with self._lock:
            if not self._count:
                return None
            # Search the two monotonic physical spans; do not materialize the
            # entire 18-second ring or copy the selected amplitude data twice.
            # Keep the exact inclusive timestamp selection and owned snapshots.
            cutoff = self.times[(self._head - 1) % self.capacity] - seconds
            spans = self._recent_spans(cutoff, side='left')
            return (np.concatenate([self.times[a:b] for a,b in spans]),
                    np.concatenate([self.amps[a:b] for a,b in spans]))

    def _recent_spans(self, cutoff, *, side):
        """Called with the ring lock held. Each returned span is monotonic."""
        oldest = (self._head - self._count) % self.capacity
        end = min(self.capacity, oldest + self._count)
        spans = [(oldest,end)]
        remaining = self._count - (end-oldest)
        if remaining:
            spans.append((0,remaining))
        return [(a + int(np.searchsorted(self.times[a:b],cutoff,side=side)),b) for a,b in spans]

    def model_window_status(self):
        """Wait for a full clean window; never interpolate across a rejected gap."""
        with self._lock:
            span = (self.times[(self._head-1) % self.capacity]-self._continuous_since
                    if self._count and self._continuous_since is not None else 0.)
            return {'ready':bool(span >= 3.02),'continuous_seconds':max(0.,float(span)),
                    'gap_events':self.input_gap_events,
                    'reason':'recovering_after_gap' if self.input_gap_events else 'warming_up'}

    def stats(self):
        with self._lock:
            if self._count < 2:
                span = hz = 0.0
            else:
                last = self.times[(self._head - 1) % self.capacity]
                first = self.times[(self._head - self._count) % self.capacity]
                span = max(0.0, last - first)
                hz = float(sum(b-a for a,b in self._recent_spans(last-1.,side='right')))
            return {
                "buffered_frames": self._count,
                "buffered_seconds": span,
                "total_frames": self.total_frames,
                "device_resets": self.device_resets,
                "sequence_gaps": self.sequence_gaps,
                "input_gap_events": self.input_gap_events,
                "hz_1s": hz,
                "last_rssi": self.last_rssi,
                "frame_age_seconds": max(0.0, time.monotonic() - self.last_host_time),
                "array_bytes": self.amps.nbytes + self.times.nbytes,
                "last_source_timestamp_seconds": float(self.times[(self._head - 1) % self.capacity])
                if self._count else None,
            }

    def get_model_window(self, seconds, selection_mode="training-fixed"):
        window = self.get_window(seconds)
        if selection_mode == "q-top30":
            if not self.keep_all:
                raise ValueError("q-top30 requires a full RAW carrier buffer")
            return window
        if selection_mode != "training-fixed":
            raise ValueError("unsupported model carrier selection")
        if window is None or not self.keep_all:
            return window
        times, amplitude = window
        return times, amplitude[:, self._indices[245]]


def prepare_window(times, amplitude, fs_hz=320.0, max_gap_seconds=0.02, *, expected_channels=30):
    """Original timestamp interpolation, without unused legacy Q/PCA signal."""
    if len(times) < 8 or amplitude.shape != (len(times), expected_channels):
        raise ValueError(f"insufficient {expected_channels}-channel CSI window")
    if not np.isfinite(times).all() or not np.isfinite(amplitude).all():
        raise ValueError("window must be finite")
    diffs = np.diff(times)
    if np.any(diffs <= 0) or float(np.max(diffs)) > max_gap_seconds:
        raise ValueError("nonmonotonic timestamp or excessive CSI gap")
    if not 300.0 <= 1.0 / float(np.median(diffs)) <= 340.0:
        raise ValueError("WGSP source rate must be between 300 and 340 Hz")
    # Request a tiny margin in the ring to cover 960 samples, then select the
    # latest exact 3-second source-rate grid. No silent padding across outages.
    grid = (
        times[0]
        + np.arange(int(np.floor((times[-1] - times[0]) * fs_hz + 1e-7)) + 1) / fs_hz
    )
    if len(grid) < int(round(3 * fs_hz)):
        raise ValueError("3 seconds of CSI are required")
    grid = grid[-int(round(3 * fs_hz)) :]
    from .interpolation import interpolate_carriers

    return interpolate_carriers(times, amplitude, grid)
