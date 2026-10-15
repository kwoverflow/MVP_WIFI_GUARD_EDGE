import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
from wifiguard_local_edge.buffer import AmplitudeBuffer
from wifiguard_local_edge.relay import LocalRelay


def frame(seq):
    return SimpleNamespace(
        seq=seq,
        timestamp_us=round(seq * 1e6 / 320),
        amps=np.arange(245, dtype=np.float32),
        host_time=time.monotonic(),
        rssi=-40,
    )


def record(seq):
    return SimpleNamespace(model_dump=lambda **_: {"seq": seq})


def test_shared_buffer_retains_presence_carriers_and_exact_model_input():
    shared = AmplitudeBuffer(seconds=12, keep_all=True)
    original = AmplitudeBuffer()
    for seq in range(4000):
        f = frame(seq)
        shared.append(f)
        original.append(f)
    assert shared.get_window(10)[1].shape == (3201, 245)
    a, b = shared.get_model_window(3.02), original.get_window(3.02)
    np.testing.assert_array_equal(a[0], b[0])
    np.testing.assert_array_equal(a[1], b[1])
    assert shared.stats()["array_bytes"] < 6_000_000
    shared.reset()
    assert shared.get_model_window(3) is None
    assert shared.get_window(10) is None


def test_durable_fifo_survives_error_and_process_restart(tmp_path):
    path = tmp_path / "outbox.db"

    def failing(body):
        raise OSError("API unavailable")

    relay = LocalRelay("http://127.0.0.1:8000", "x" * 48, path, sender=failing)
    relay.offer("inference", record(1), durable=True)
    assert relay.flush_once() is False
    assert relay.status()["durable_pending"] == 1
    relay.stop()
    sent = []
    second = LocalRelay(
        "http://127.0.0.1:8000",
        "x" * 48,
        path,
        sender=lambda b: sent.append(json.loads(b)),
    )
    assert second.flush_once()
    assert sent[0]["record"]["seq"] == 1
    assert second.status()["durable_pending"] == 0
    second.stop()


def test_live_queue_coalesces_and_durable_overflow_is_not_silent(tmp_path):
    relay = LocalRelay(
        "http://localhost:8000", "x" * 48, tmp_path / "outbox.db", capacity=1
    )
    for seq in range(100):
        relay.offer("presence", record(seq), key="presence")
    relay.offer("inference", record(1), durable=True)
    with pytest.raises(RuntimeError, match="full"):
        relay.offer("inference", record(2), durable=True)
    status = relay.status()
    assert status["live_pending"] == 1 and status["coalesced_live_samples"] == 99
    assert status["overflow"] == 1
    relay.stop()


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.1:8000",
        "https://localhost:8000",
        "http://secret@localhost:8000",
        "http://localhost:8000?secret=x",
    ],
)
def test_relay_requires_loopback_without_embedded_secrets(tmp_path, url):
    with pytest.raises(ValueError):
        LocalRelay(url, "x" * 48, tmp_path / "outbox.db")
