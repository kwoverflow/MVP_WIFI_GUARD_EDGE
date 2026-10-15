"""Bounded same-host relay: inference on disk, replaceable live samples in RAM.

SQLite is ONLY an outbox, not a replacement for the PR PostgreSQL/Timescale DB.
No networking runs on the SPI, feature or presence threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class LocalRelay(threading.Thread):
    def __init__(self, url, token, outbox: Path, *, capacity=4096, sender=None):
        super().__init__(daemon=True, name="pi5-backend-relay")
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
        self._latencies = []
        self._sender = sender or self._http_send
        self._opener = build_opener(
            ProxyHandler({})
        )  # never forward token through an environment proxy
        outbox.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(outbox, check_same_thread=False, timeout=2)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        self._db.commit()

    def offer(self, topic, record, *, durable=False, key=None):
        body = json.dumps(
            {"topic": topic, "record": record.model_dump(mode="json")},
            separators=(",", ":"),
        )
        with self._lock:
            if durable:
                count = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
                if count >= self.capacity:
                    self._overflow += 1
                    self._last_error = "durable outbox full; result not accepted"
                    raise RuntimeError(self._last_error)
                self._db.execute("INSERT INTO outbox(body) VALUES (?)", (body,))
                self._db.commit()
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
            row = self._db.execute(
                "SELECT id,body FROM outbox ORDER BY id LIMIT 1"
            ).fetchone()
            live = next(iter(self._live.items()), None)
        delivered = False
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
                    self._db.execute("DELETE FROM outbox WHERE id=?", (row[0],))
                    self._db.commit()
                elif self._live.get(live[0]) == body:
                    del self._live[live[0]]
                self._sent += 1
                self._last_error = None
                self._latencies.append((time.perf_counter() - started) * 1000)
                del self._latencies[:-4096]
                delivered = True
        return delivered

    def run(self):
        while not self._stop_event.is_set():
            sent = self.flush_once()
            self._stop_event.wait(0.01 if sent else 0.2)

    def status(self):
        from .metrics import distribution

        with self._lock:
            pending = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            return {
                "sent": self._sent,
                "errors": self._errors,
                "overflow": self._overflow,
                "durable_pending": pending,
                "live_pending": len(self._live),
                "coalesced_live_samples": self._coalesced,
                "last_error": self._last_error,
                "delivery_ms": distribution(self._latencies),
            }

    def stop(self):
        self._stop_event.set()
        if self.ident is not None:
            self.join(5)
        if self.is_alive():
            raise RuntimeError("relay did not stop; outbox retained")
        with self._lock:
            self._db.close()
