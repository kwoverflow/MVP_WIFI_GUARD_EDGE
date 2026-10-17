import json
import threading
import time

import pytest

from wifiguard_local_edge.status_writer import LatestStatusWriter


def test_bounded_live_snapshots_never_block_detection_and_close_drains(tmp_path):
    entered, release = threading.Event(), threading.Event()
    written=[]
    def slow_write(path,snapshot,**kwargs):
        assert kwargs == {'indent':None}
        entered.set()
        assert release.wait(2)
        written.append(snapshot['counter'])
    writer=LatestStatusWriter(tmp_path/'status.json',write=slow_write)
    writer.publish({'counter':0})
    assert entered.wait(2)
    started=time.perf_counter()
    for number in range(1,100):
        writer.publish({'counter':number})
    assert time.perf_counter()-started < .1
    assert writer.status()['coalesced_snapshots'] == 98
    release.set()
    writer.close()
    assert written == [0,99]
    with pytest.raises(RuntimeError,match='closed'):
        writer.publish({})


def test_final_status_cannot_be_overwritten_by_older_live_snapshot(tmp_path):
    from wifiguard_local_edge.metrics import atomic_json
    path=tmp_path/'status.json'
    writer=LatestStatusWriter(path)
    writer.publish({'running':True,'counters':{'inferences':12}})
    writer.close()
    atomic_json(path,{'running':False})
    assert json.loads(path.read_text()) == {'running':False}
    writer.close()  # idempotent shutdown


def test_status_write_error_is_not_silenced(tmp_path):
    attempted=threading.Event()
    def failure(*args,**kwargs):
        attempted.set()
        raise OSError('SD failure')
    writer=LatestStatusWriter(tmp_path/'status.json',write=failure)
    writer.publish({'running':True})
    assert attempted.wait(2)
    with pytest.raises(RuntimeError,match='status write failed'):
        writer.close()
