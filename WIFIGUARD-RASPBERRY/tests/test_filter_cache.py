import numpy as np
from scipy import signal

from wifiguard_edge.presence.preprocessing import _bandpass_coefficients, bandpass_filter


def test_cached_design_preserves_exact_filter_output_and_config_changes():
    values = np.random.default_rng(14).normal(size=(960, 245)).astype(np.float32)
    _bandpass_coefficients.cache_clear()
    for low, high in ((.5, 49), (.05, 5), (.5, 49)):
        sos = signal.butter(4, [low, high], btype="band", fs=100, output="sos")
        expected = signal.sosfiltfilt(sos, values, axis=0).astype(np.float32)
        np.testing.assert_array_equal(bandpass_filter(values, 100, low, high), expected)
    info = _bandpass_coefficients.cache_info()
    assert info.misses == 2 and info.hits == 1 and info.maxsize == 32
    assert not _bandpass_coefficients(100, .5, 49, 4).flags.writeable
