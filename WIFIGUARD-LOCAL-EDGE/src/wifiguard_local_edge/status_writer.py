"""One-slot live status writer, separate from durable inference persistence.

Only replaceable monitoring snapshots are coalesced. This queue MUST NOT carry
inference results, presence DB records, alarms, or durable outbox entries.
"""
from __future__ import annotations

import threading
import time

from .metrics import atomic_json


class LatestStatusWriter:
    def __init__(self, path, *, write=atomic_json):
        self.path, self._write = path, write
        self._condition = threading.Condition()
        self._pending = None
        self._closing = False
        self._error = None
        self._coalesced = self._written = 0
        self._last_ms = 0.
        self._worker = threading.Thread(target=self._run, name='pi5-live-status', daemon=True)
        self._worker.start()

    def _check(self):
        if self._error is not None:
            raise RuntimeError('live status write failed; durable outbox is separate') from self._error

    def publish(self, snapshot):
        with self._condition:
            self._check()
            if self._closing:
                raise RuntimeError('live status writer is closed')
            self._coalesced += int(self._pending is not None)
            self._pending = snapshot
            self._condition.notify()

    def status(self):
        with self._condition:
            self._check()
            return {'scope':'replaceable status.json only; never durable records',
                    'written':self._written,'coalesced_snapshots':self._coalesced,
                    'pending':self._pending is not None,'last_write_ms':self._last_ms}

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda:self._pending is not None or self._closing)
                    if self._pending is None:
                        return
                    snapshot, self._pending = self._pending, None
                started = time.perf_counter()
                self._write(self.path, snapshot, indent=None)
                with self._condition:
                    self._last_ms = (time.perf_counter()-started)*1000
                    self._written += 1
        except Exception as exc:
            with self._condition:
                self._error = exc
                self._pending = None

    def close(self):
        with self._condition:
            self._closing = True
            self._condition.notify()
        self._worker.join(5)
        if self._worker.is_alive():
            raise RuntimeError('live status writer did not stop')
        with self._condition:
            self._check()
