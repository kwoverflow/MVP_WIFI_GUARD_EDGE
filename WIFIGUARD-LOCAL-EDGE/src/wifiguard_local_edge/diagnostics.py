"""Bounded failure evidence, without RAW CSI payloads or changed quality limits."""
from collections import Counter, deque
import threading
import time

import numpy as np


class StreamDiagnostics:
    def __init__(self):
        self._lock = threading.Lock()
        self._previous = None
        self._latest = {}
        self._gaps = deque(maxlen=16)
        self._timestamp_gaps = deque(maxlen=16)
        self._counter_changes = deque(maxlen=16)

    def observe(self, frame, capture_context=None):
        current = {
            'epoch': frame.stream_epoch, 'seq': frame.frame_seq,
            'source_timestamp_us': frame.source_timestamp_us,
            **{name: getattr(frame, name) for name in (
                'source_dropped_total', 'queue_dropped_total',
                'spi_dropped_total', 'transport_errors_total')},
        }
        with self._lock:
            previous = self._previous
            if previous and current['epoch'] == previous['epoch']:
                gap = current['seq'] - previous['seq'] - 1
                delta_us = current['source_timestamp_us'] - previous['source_timestamp_us']
                increases = {name:current[name]-previous[name] for name in (
                    'source_dropped_total','queue_dropped_total','spi_dropped_total','transport_errors_total')}
                # Callback sequence and RX timestamp answer different questions:
                # a radio/callback timing pause can have consecutive sequences.
                if delta_us <= 0 or delta_us > 20_000:
                    self._timestamp_gaps.append({
                        'wall_time_unix':time.time(),'source_gap_ms':delta_us/1000,
                        'previous_seq':previous['seq'],'next_received_seq':current['seq'],
                        'sequence_consecutive':gap==0,'missing_sequence_frames':max(0,gap),
                        'capture_context':dict(capture_context or {}),
                    })
                # Firmware packs counters into a batch header before later
                # losses occur. Preserve delayed increases, not only gap frames.
                if any(value>0 for value in increases.values()):
                    self._counter_changes.append({
                        'wall_time_unix':time.time(),'seq':current['seq'],
                        'increases':increases,'capture_context':dict(capture_context or {}),
                    })
                if gap > 0:
                    self._gaps.append({
                        'wall_time_unix': time.time(), 'missing_frames': gap,
                        'source_gap_ms': (current['source_timestamp_us'] - previous['source_timestamp_us']) / 1000,
                        'first_missing_seq': previous['seq'] + 1, 'next_received_seq': current['seq'],
                        'capture_context': dict(capture_context or {}),
                        'receiver_counter_increases': {name: current[name] - previous[name] for name in (
                            'source_dropped_total', 'queue_dropped_total',
                            'spi_dropped_total', 'transport_errors_total')},
                    })
            self._previous = current
            self._latest = current

    def status(self):
        with self._lock:
            return {'latest_receiver': dict(self._latest), 'recent_sequence_gaps': list(self._gaps),
                    'recent_timestamp_discontinuities':list(self._timestamp_gaps),
                    'recent_receiver_counter_increases':list(self._counter_changes)}


class CaptureDiagnostics:
    """Measure host-side stalls separately from firmware counters, never RAW IQ."""
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = Counter()
        self._timings = {name:deque(maxlen=2048) for name in
                         ('ready_wait_ms','transfer_ms','handshake_ms','decode_append_ms','inter_transfer_ms')}
        self._events = deque(maxlen=16)

    def observe(self, timing):
        slow = any((timing.get(key) or 0) > limit for key,limit in
                   (('inter_transfer_ms',50),('transfer_ms',20),('handshake_ms',20),('decode_append_ms',20)))
        with self._lock:
            self._counts['batches'] += 1
            for key in ('handshake_timeout','transport_error'):
                self._counts[key] += int(bool(timing.get(key)))
            self._counts['slow_batches'] += int(slow)
            for key, values in self._timings.items():
                if timing.get(key) is not None:
                    values.append(float(timing[key]))
            if slow or timing.get('handshake_timeout') or timing.get('transport_error') or timing.get('protocol_rejects'):
                self._events.append(dict(timing))

    def status(self):
        from .metrics import distribution
        with self._lock:
            counts = dict(self._counts)
            timings = {key:list(values) for key,values in self._timings.items()}
            events = list(self._events)
        return {'counts':counts, 'timings_ms':{key:distribution(values) for key,values in timings.items()},
                'recent_events':events, 'scope':'host timings; correlate with receiver counters, not proof of radio loss'}


class LoopDiagnostics:
    def __init__(self):
        self._events = deque(maxlen=24)
        self._counts = Counter()

    def record(self, kind, **details):
        if 'source' in details:
            source = details['source']
            diagnostics = source.get('diagnostics') or {}
            details['source'] = {key:source.get(key) for key in ('frames_ok','protocol_rejects','transport_errors')}
            details['source']['latest_receiver'] = diagnostics.get('latest_receiver')
            details['source']['latest_sequence_gap'] = (diagnostics.get('recent_sequence_gaps') or [None])[-1]
        self._counts[kind] += 1
        self._events.append({'kind': kind, 'wall_time_unix': time.time(), **details})

    def rejected_window(self, window, reason, source):
        times = window[0]
        diffs = np.diff(times)
        self.record('invalid_window', reason=str(reason), samples=len(times),
                    max_timestamp_gap_ms=float(diffs.max() * 1000) if len(diffs) else None,
                    source_start_seconds=float(times[0]) if len(times) else None,
                    source_end_seconds=float(times[-1]) if len(times) else None,
                    source=source)

    def status(self):
        return {'counts': dict(self._counts), 'recent_events': list(self._events)}
