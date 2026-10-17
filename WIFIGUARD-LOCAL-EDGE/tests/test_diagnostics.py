from types import SimpleNamespace
import numpy as np

from wifiguard_local_edge.diagnostics import CaptureDiagnostics, LoopDiagnostics, StreamDiagnostics


def test_receiver_gap_evidence_keeps_counter_causes_and_is_bounded():
    diagnostics = StreamDiagnostics()
    for index in range(40):
        diagnostics.observe(SimpleNamespace(stream_epoch=1,frame_seq=index*4,
            source_timestamp_us=index*12500,source_dropped_total=0,
            queue_dropped_total=index,spi_dropped_total=0,transport_errors_total=0))
    result = diagnostics.status()
    assert len(result['recent_sequence_gaps']) == 16
    assert result['recent_sequence_gaps'][-1]['missing_frames'] == 3
    assert result['recent_sequence_gaps'][-1]['receiver_counter_increases']['queue_dropped_total'] == 1
    diagnostics.observe(SimpleNamespace(stream_epoch=2,frame_seq=0,source_timestamp_us=1,
        source_dropped_total=0,queue_dropped_total=0,spi_dropped_total=0,transport_errors_total=0))
    assert len(diagnostics.status()['recent_sequence_gaps']) == 16


def test_window_rejections_keep_reason_without_retaining_raw_amplitude():
    diagnostics = LoopDiagnostics()
    times = np.array([0.,.003,.1])
    for _ in range(30):
        diagnostics.rejected_window((times,np.zeros((3,245))),'excessive CSI gap',{})
    result = diagnostics.status()
    assert result['counts']['invalid_window'] == 30
    assert len(result['recent_events']) == 24
    assert result['recent_events'][-1]['max_timestamp_gap_ms'] == 97
    assert 'amplitude' not in result['recent_events'][-1]


def test_capture_evidence_distinguishes_handshake_and_processing_and_is_bounded():
    diagnostics = CaptureDiagnostics()
    for index in range(2200):
        diagnostics.observe({'wall_time_unix':index, 'transfer_ms':7., 'ready_wait_ms':0.,
                             'handshake_ms':100., 'handshake_timeout':True,
                             'decode_append_ms':.7, 'inter_transfer_ms':108., 'first_frame_seq':index*8})
    status = diagnostics.status()
    assert status['counts'] == {'batches':2200, 'handshake_timeout':2200, 'transport_error':0, 'slow_batches':2200}
    assert len(status['recent_events']) == 16
    assert status['timings_ms']['transfer_ms']['count'] == 2048
    assert status['timings_ms']['handshake_ms']['median'] == 100.
    assert 'amplitude' not in str(status)


def test_sequence_gap_captures_host_batch_context_without_aliasing():
    diagnostics = StreamDiagnostics()
    def frame(seq):
        return SimpleNamespace(stream_epoch=1,frame_seq=seq,source_timestamp_us=seq*3125,
                               source_dropped_total=0,queue_dropped_total=0,spi_dropped_total=0,transport_errors_total=0)
    diagnostics.observe(frame(0))
    context = {'handshake_timeout':True,'handshake_ms':100.}
    diagnostics.observe(frame(8),context)
    context['handshake_ms'] = 1.
    gap = diagnostics.status()['recent_sequence_gaps'][-1]
    assert gap['missing_frames'] == 7 and gap['capture_context']['handshake_ms'] == 100.


def test_timestamp_pause_and_delayed_drop_counters_are_distinguished():
    diagnostics=StreamDiagnostics()
    def frame(seq,ts,queue=0):
        return SimpleNamespace(stream_epoch=1,frame_seq=seq,source_timestamp_us=ts,
            source_dropped_total=0,queue_dropped_total=queue,spi_dropped_total=0,transport_errors_total=0)
    diagnostics.observe(frame(0,0))
    diagnostics.observe(frame(1,50_000))
    state=diagnostics.status()
    assert state['recent_sequence_gaps'] == []
    assert state['recent_timestamp_discontinuities'][-1]['sequence_consecutive'] is True
    diagnostics.observe(frame(8,71_875))
    diagnostics.observe(frame(9,75_000,6))  # Counter increase arrives one frame later.
    state=diagnostics.status()
    assert state['recent_sequence_gaps'][-1]['missing_frames'] == 6
    assert state['recent_receiver_counter_increases'][-1]['increases']['queue_dropped_total'] == 6
