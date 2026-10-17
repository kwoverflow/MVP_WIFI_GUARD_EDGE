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


def test_full_commit_survives_abrupt_process_exit_without_close(tmp_path):
    import subprocess
    import sys
    path=tmp_path/'outbox.db'
    script='''
import os,sys
from types import SimpleNamespace
from pathlib import Path
from wifiguard_local_edge.relay import LocalRelay
relay=LocalRelay('http://localhost:8000','x'*48,Path(sys.argv[1]))
relay.offer('inference',SimpleNamespace(model_dump=lambda **_: {'seq':71}),durable=True)
os._exit(0)
'''
    subprocess.run([sys.executable,'-c',script,str(path)],check=True,capture_output=True,timeout=15)
    sent=[]
    relay=LocalRelay('http://localhost:8000','x'*48,path,sender=lambda b:sent.append(json.loads(b)))
    assert relay.status()['durable_pending'] == 1
    assert relay.flush_once()
    assert sent[0]['record']['seq'] == 71
    relay.stop()


@pytest.mark.parametrize('rollback_fails',[False,True])
def test_failed_commit_cannot_be_sent_or_reported_as_a_durable_success(tmp_path,rollback_fails):
    import sqlite3
    sent=[]
    relay=LocalRelay('http://localhost:8000','x'*48,tmp_path/'outbox.db',sender=sent.append)
    real_db=relay._db
    class FailedCommit:
        def __getattr__(self,name):
            return getattr(real_db,name)
        def commit(self):
            raise sqlite3.OperationalError('simulated disk full')
        def rollback(self):
            if rollback_fails:
                raise sqlite3.OperationalError('simulated rollback failure')
            real_db.rollback()
    relay._db=FailedCommit()
    with pytest.raises(RuntimeError,match='commit failed; result not accepted'):
        relay.offer('inference',record(17),durable=True)
    assert relay.status()['durable_pending'] == 0
    assert relay.status()['durable_fault'] is True
    assert relay.status()['durable_enqueue_ms']['count'] == 0
    assert not relay.flush_once() and sent == []
    with pytest.raises(RuntimeError,match='requires recovery'):
        relay.offer('inference',record(18),durable=True)
    relay.offer('presence',record(3),key='presence')
    assert relay.flush_once()
    assert relay.status()['durable_fault'] and 'commit failed' in relay.status()['last_error']
    relay.stop()


def test_only_ack_cleanup_uses_normal_sync_and_writer_remains_full(tmp_path):
    relay = LocalRelay('http://localhost:8000', 'x'*48, tmp_path/'outbox.db', sender=lambda _: None)
    assert relay._db.execute('PRAGMA synchronous').fetchone()[0] == 2
    assert relay._ack_db.execute('PRAGMA synchronous').fetchone()[0] == 1
    relay.offer('inference', record(1), durable=True)
    assert relay.flush_once()
    relay.offer('inference', record(2), durable=True)
    assert relay._db.execute('PRAGMA synchronous').fetchone()[0] == 2
    assert relay.status()['durable_pending'] == 1
    assert relay.status()['durable_enqueue_ms']['count'] == 2
    assert relay.status()['ack_cleanup_ms']['count'] == 1
    relay.stop()


def test_lost_ack_replays_same_identity_without_dropping_fifo(tmp_path):
    attempts = []
    def lost_ack(body):
        attempts.append(json.loads(body)['record']['seq'])
        if len(attempts) == 1:
            raise RuntimeError('backend committed, HTTP ACK was lost')
    relay = LocalRelay('http://localhost:8000','x'*48,tmp_path/'outbox.db',sender=lost_ack)
    relay.offer('inference',record(41),durable=True)
    assert not relay.flush_once() and relay.status()['durable_pending'] == 1
    assert relay.flush_once() and relay.status()['durable_pending'] == 0
    assert attempts == [41,41]
    relay.stop()


def test_checkpoint_is_separate_and_does_not_lower_full_writer_sync(tmp_path):
    relay = LocalRelay('http://localhost:8000','x'*48,tmp_path/'outbox.db',sender=lambda _:None,checkpoint_every=2)
    for seq in range(4):
        relay.offer('inference',record(seq),durable=True)
        assert relay.flush_once()
    assert relay.status()['checkpoints'] == 2
    assert relay.status()['checkpoint_errors'] == 0
    assert relay._db.execute('PRAGMA wal_autocheckpoint').fetchone()[0] == 0
    assert relay._db.execute('PRAGMA synchronous').fetchone()[0] == 2
    relay.stop()


def test_commit_latency_is_measured_without_weakening_durable_ack(tmp_path):
    import sqlite3
    relay = LocalRelay('http://localhost:8000','x'*48,tmp_path/'outbox.db',sender=lambda _:None)
    real_db = relay._db
    class SlowCommit:
        def __getattr__(self,name):
            return getattr(real_db,name)
        def commit(self):
            time.sleep(.06)
            real_db.commit()
    relay._db = SlowCommit()
    relay.offer('inference',record(13),durable=True)
    # A separate reader sees the committed row BEFORE offer returns to its caller.
    with sqlite3.connect(tmp_path/'outbox.db') as other:
        assert json.loads(other.execute('SELECT body FROM outbox').fetchone()[0])['record']['seq'] == 13
    state = relay.status()
    phases = state['durable_enqueue_phases_ms']
    assert phases['full_commit']['max'] >= 60
    assert phases['insert']['max'] < phases['full_commit']['max']
    assert state['recent_slow_enqueues'][-1]['total_ms'] >= 60
    assert 'body' not in state['recent_slow_enqueues'][-1]
    assert real_db.execute('PRAGMA synchronous').fetchone()[0] == 2
    relay.stop()


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
