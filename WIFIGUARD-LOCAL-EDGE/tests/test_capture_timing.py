"""The diagnostic hook must neither alter batch data nor skip the handshake."""
from types import SimpleNamespace

from wifiguard_edge.transport import wgsp_source as transport
from wifiguard_local_edge.diagnostics import CaptureDiagnostics


def test_batch_timing_keeps_ready_transfer_decode_order(monkeypatch):
    calls, observations, appended = [], [], []
    class Spi:
        def __init__(self, **kwargs):
            pass
        def transfer(self, count):
            calls.append('transfer')
            assert count == 4608
            return b''.join(bytes([i])*576 for i in range(8))
        def close(self):
            calls.append('close')
    monkeypatch.setattr(transport,'LinuxDirectSpi',Spi)
    monkeypatch.setattr(transport,'decode_spi_csi_frame',lambda packet:SimpleNamespace(frame_seq=packet[0]))
    config = SimpleNamespace(spi_batch_frames=8,spi_bus=0,spi_device=0,max_speed_hz=6000000)
    source = transport.WgspBatch8Source(None,config)
    source._open_ready = lambda:SimpleNamespace(release=lambda:calls.append('release'))
    source._wait_ready = lambda:True
    def handshake():
        calls.append('handshake')
        return True
    source._wait_transaction_edge = handshake
    def append(frame):
        assert calls[-1] == 'handshake'
        appended.append(frame.frame_seq)
        if frame.frame_seq == 7:
            source.stop()
    source._append = append
    source._observe_batch = observations.append
    source.run()
    assert appended == list(range(8))
    assert calls == ['transfer','handshake','close','release']
    assert len(observations) == 1
    sample = observations[0]
    assert sample['valid_frames'] == 8 and sample['protocol_rejects'] == 0
    assert sample['first_frame_seq'] == 0 and sample['last_frame_seq'] == 7
    assert sample['handshake_timeout'] is False
    assert all(sample[key]>=0 for key in ('transfer_ms','handshake_ms','decode_append_ms','ready_wait_ms'))
    diagnostics = CaptureDiagnostics()
    diagnostics.observe(sample)
    assert diagnostics.status()['counts']['batches'] == 1


def test_handshake_timeout_is_visible_but_preserves_original_retry_semantics(monkeypatch):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(transport,'time',SimpleNamespace(monotonic=lambda:clock.now))
    source = transport.WgspBatch8Source(None,SimpleNamespace(spi_batch_frames=8))
    def wait(timeout):
        clock.now += timeout
        return False
    source._ready_request = SimpleNamespace(wait_edge_events=wait)
    assert source._wait_transaction_edge() is True
    assert source._last_handshake_timed_out is True
    assert clock.now >= .1
    source.stop()
    assert source._wait_transaction_edge() is False
    assert source._last_handshake_timed_out is False


def test_ready_level_is_rechecked_within_five_ms_even_without_an_edge():
    source = transport.WgspBatch8Source(None,SimpleNamespace(spi_batch_frames=8))
    ready = iter([False,True])
    source._is_ready = lambda:next(ready)
    waits=[]
    def wait(timeout):
        waits.append(timeout)
        return False
    source._ready_request=SimpleNamespace(wait_edge_events=wait)
    assert source._wait_ready()
    assert waits == [.005,0]
    assert source._last_ready_wait['ready_poll_timeouts'] == 1
    assert source._last_ready_wait['ready_edge_wakeups'] == 0
