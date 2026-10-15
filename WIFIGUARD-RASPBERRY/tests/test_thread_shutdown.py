"""Thread subclasses must remain joinable during service shutdown."""

from __future__ import annotations

from wifiguard_edge.csi.buffer import RingBuffer
from wifiguard_edge.csi.serial_reader import SerialReader
from wifiguard_edge.feature_loop import FeatureLoop
from wifiguard_edge.features.realtime import FeatureConfig
from wifiguard_edge.gating import SignalGate
from wifiguard_edge.presence import PresenceConfig
from wifiguard_edge.presence_loop import PresenceLoop
from wifiguard_edge.transport.replay_source import ReplaySource


def _threads():
    ring = RingBuffer(5.0)
    return [
        FeatureLoop(ring, FeatureConfig(), SignalGate(), lambda *_: None),
        PresenceLoop(ring, PresenceConfig()),
        SerialReader(ring),
        ReplaySource(ring),
    ]


def test_thread_subclasses_do_not_shadow_private_stop_method():
    """threading.Thread.join() internally calls the bound ``_stop()`` method."""
    for worker in _threads():
        assert callable(worker._stop), f"{type(worker).__name__} shadows Thread._stop()"  # type: ignore[attr-defined]


def test_processing_threads_stop_and_join_cleanly():
    for worker in _threads()[:2]:
        worker.start()
        worker.stop()
        worker.join(timeout=2.0)
        assert not worker.is_alive()
