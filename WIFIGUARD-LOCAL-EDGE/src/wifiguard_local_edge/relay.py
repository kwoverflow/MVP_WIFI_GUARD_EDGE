"""Bounded same-host relay: inference on disk, replaceable live samples in RAM.

SQLite is ONLY an outbox, not a replacement for the PR PostgreSQL/Timescale DB.
No networking runs on the SPI, feature or presence threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class LocalRelay(threading.Thread):
    def __init__(self, url, token, outbox: Path, *, capacity=4096, sender=None, checkpoint_every=64):
        super().__init__(daemon=True, name="pi5-backend-relay")
        if checkpoint_every < 1:
            raise ValueError("positive checkpoint period required")
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError(
                "local backend URL must use HTTP loopback, never LAN/cloud"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("backend URL must not contain credentials/query/fragment")
        if len(token.encode()) < 32:
            raise ValueError("LOCAL_EDGE_TOKEN must contain at least 32 bytes")
        self.url, self.token = url.rstrip("/") + "/internal/edge/record", token
        self.capacity = capacity
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._live = OrderedDict()
        self._sent = self._errors = self._overflow = self._coalesced = 0
        self._last_error = None
        self._durable_fault = False
        self._latencies = []
        self._sender = sender or self._http_send
        self._opener = build_opener(
            ProxyHandler({})
        )  # never forward token through an environment proxy
        outbox.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(outbox, check_same_thread=False, timeout=2)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA wal_autocheckpoint=0")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        self._db.commit()
        # New results still return only after a FULL-synchronous WAL commit.
        # ACK deletion is replay-safe: after a power failure an acknowledged row
        # may reappear, but the backend's durable idempotency key rejects it.
        # A separate connection avoids a second fsync per result without ever
        # lowering the writer connection's durability.
        self._ack_db = sqlite3.connect(outbox, check_same_thread=False, timeout=2)
        self._ack_db.execute("PRAGMA synchronous=NORMAL")
        self._ack_db.execute("PRAGMA wal_autocheckpoint=0")
        self._checkpoint_every = checkpoint_every
        self._acks = self._checkpoints = self._checkpoint_errors = 0
        self._checkpoint_latencies = []
        assert self._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert self._db.execute("PRAGMA synchronous").fetchone()[0] == 2
        self._enqueue_latencies = []
        self._ack_latencies = []
        self._enqueue_phases = {name: deque(maxlen=4096) for name in ('serialize', 'lock_wait', 'insert', 'full_commit')}
        self._slow_enqueues = deque(maxlen=16)

    def offer(self, topic, record, *, durable=False, key=None):
        started = time.perf_counter()
        body = json.dumps(
            {"topic": topic, "record": record.model_dump(mode="json")},
            separators=(",", ":"),
        )
        serialized = time.perf_counter()
        with self._lock:
            if durable:
                locked = time.perf_counter()
                if self._durable_fault:
                    raise RuntimeError("durable outbox requires recovery; result not accepted")
                count = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
                if count >= self.capacity:
                    self._overflow += 1
                    self._last_error = "durable outbox full; result not accepted"
                    raise RuntimeError(self._last_error)
                try:
                    self._db.execute("INSERT INTO outbox(body) VALUES (?)", (body,))
                    inserted = time.perf_counter()
                    self._db.commit()
                except Exception as exc:
                    # Another thread must never SELECT/send an uncommitted row
                    # through this shared connection after a failed fsync.
                    self._durable_fault = True
                    self._errors += 1
                    self._last_error = "durable outbox commit failed; result not accepted"
                    try:
                        self._db.rollback()
                    except sqlite3.Error:
                        pass  # Fault remains latched; restart/recovery is required.
                    raise RuntimeError(self._last_error) from exc
                committed = time.perf_counter()
                phases = dict(zip(self._enqueue_phases, (
                    (serialized-started)*1000, (locked-serialized)*1000,
                    (inserted-locked)*1000, (committed-inserted)*1000,
                )))
                for name, value in phases.items():
                    self._enqueue_phases[name].append(value)
                total_ms = (committed-started)*1000
                if total_ms > 50:
                    self._slow_enqueues.append({'wall_time_unix':time.time(), 'total_ms':total_ms, 'phases_ms':phases})
                self._enqueue_latencies.append(total_ms)
                del self._enqueue_latencies[:-4096]
            else:
                if key not in ("presence", "telemetry"):
                    raise ValueError(
                        "live records require a bounded presence/telemetry key"
                    )
                self._coalesced += int(key in self._live)
                self._live[key] = body

    def _http_send(self, body):
        request = Request(
            self.url,
            body.encode(),
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.token,
            },
        )
        with self._opener.open(request, timeout=2) as response:
            if (
                response.status != 200
                or json.load(response).get("accepted") is not True
            ):
                raise RuntimeError("backend did not ACK the record")

    def flush_once(self):
        with self._lock:
            row = None if self._durable_fault else self._db.execute(
                "SELECT id,body FROM outbox ORDER BY id LIMIT 1"
            ).fetchone()
            live = next(iter(self._live.items()), None)
        delivered = False
        checkpoint_due = False
        # Live state is refreshed even while the durable FIFO is draining.
        for identity, body in ([("live", live[1])] if live else []) + (
            [("durable", row[1])] if row else []
        ):
            started = time.perf_counter()
            try:
                self._sender(body)
            except Exception as exc:  # noqa: BLE001 - retain the outbox on any failed ACK
                with self._lock:
                    self._errors += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                return delivered
            with self._lock:
                if identity == "durable":
                    ack_started = time.perf_counter()
                    self._ack_db.execute("DELETE FROM outbox WHERE id=?", (row[0],))
                    self._ack_db.commit()
                    self._ack_latencies.append((time.perf_counter() - ack_started) * 1000)
                    del self._ack_latencies[:-4096]
                    self._acks += 1
                    checkpoint_due = self._acks % self._checkpoint_every == 0
                elif self._live.get(live[0]) == body:
                    del self._live[live[0]]
                self._sent += 1
                if not self._durable_fault:
                    self._last_error = None
                self._latencies.append((time.perf_counter() - started) * 1000)
                del self._latencies[:-4096]
                delivered = True
        if checkpoint_due:
            # PASSIVE maintenance is performed by the sender, OUTSIDE the
            # model enqueue lock. Foreground commits remain WAL+FULL. Never
            # run an automatic checkpoint while an inference holds this lock.
            started = time.perf_counter()
            try:
                self._ack_db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
                with self._lock:
                    self._checkpoints += 1
                    self._checkpoint_latencies.append((time.perf_counter() - started) * 1000)
                    del self._checkpoint_latencies[:-128]
            except sqlite3.Error:
                with self._lock:
                    self._checkpoint_errors += 1
        return delivered

    def run(self):
        while not self._stop_event.is_set():
            sent = self.flush_once()
            self._stop_event.wait(0.01 if sent else 0.2)

    def status(self):
        from .metrics import distribution

        with self._lock:
            pending_db = self._ack_db if self._durable_fault else self._db
            pending = pending_db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            return {
                "sent": self._sent,
                "errors": self._errors,
                "overflow": self._overflow,
                "durable_pending": pending,
                "live_pending": len(self._live),
                "coalesced_live_samples": self._coalesced,
                "last_error": self._last_error,
                "durable_fault": self._durable_fault,
                "delivery_ms": distribution(self._latencies),
                "durable_enqueue_ms": distribution(self._enqueue_latencies),
                "durable_enqueue_phases_ms": {key:distribution(list(values)) for key,values in self._enqueue_phases.items()},
                "recent_slow_enqueues": list(self._slow_enqueues),
                "ack_cleanup_ms": distribution(self._ack_latencies),
                "checkpoint_ms": distribution(self._checkpoint_latencies),
                "checkpoints": self._checkpoints,
                "checkpoint_errors": self._checkpoint_errors,
                "durability": "WAL FULL before offer returns; ACK cleanup NORMAL, power-loss replay deduplicated by backend",
            }

    def stop(self):
        self._stop_event.set()
        if self.ident is not None:
            self.join(5)
        if self.is_alive():
            raise RuntimeError("relay did not stop; outbox retained")
        with self._lock:
            self._ack_db.close()
            self._db.close()
