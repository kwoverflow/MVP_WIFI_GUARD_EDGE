"""`extract_window_features` 3분해가 출력을 바꾸지 않았음을 단언한다 (D1).

분해의 목적은 엣지가 `extract_window_signal` 까지만, 클라우드가 `features_from_signal`
부터 돌리는 것이다. 두 조각을 이어 붙인 결과가 분해 이전과 **비트 단위로 같아야** 한다 —
여기서 어긋나면 학습 데이터와 피처 분포가 달라져 모델 성능이 무효가 된다
(`features/common.py` 상단 주석).

`extract_window_features` 자체가 이제 합성이므로, 이 테스트는 "합성 == 수동 2단계"를 본다.
분해 이전 구현과의 대조는 골든 픽스처가 없어 불가능하지만, 절단점이
`select_pc_signal()` 의 반환값이고 그 함수를 한 줄도 고치지 않았으므로 구조적으로 보장된다.
"""

from __future__ import annotations

import numpy as np
import pytest

from wifiguard_edge.features import (
    FeatureConfig,
    extract_window_features,
    extract_window_signal,
    features_from_signal,
)


def synth(fs_hz: float = 166.75, seconds: float = 3.4, subcarriers: int = 245, seed: int = 7):
    """합성 윈도우. tools/bench_pipeline.py 와 같은 방식."""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs_hz)
    times = np.arange(n, dtype=np.float64) / fs_hz
    t = times[:, None]
    base = 20.0 + 4.0 * np.sin(2 * np.pi * 1.7 * t + np.linspace(0, 3, subcarriers)[None, :])
    amp = (base + rng.normal(0, 0.35, size=(n, subcarriers))).astype(np.float32)
    return times, amp


@pytest.fixture(scope="module")
def window():
    return synth()


def test_split_matches_combined_bit_for_bit(window):
    """엣지+클라우드 2단계 == 기존 단일 호출."""
    times, amp = window
    cfg = FeatureConfig()

    combined = extract_window_features(times, amp, cfg)

    ws = extract_window_signal(times, amp, cfg)
    manual = features_from_signal(ws.signal, ws.fs_hz, cfg)

    assert np.array_equal(combined.s3, manual.s3), "S3 가 달라졌다"
    assert np.array_equal(combined.acf, manual.acf), "PCA-ACF 가 달라졌다"
    assert combined.fs_hz == manual.fs_hz == ws.fs_hz


def test_combined_stats_keys_are_union_of_both_halves(window):
    """stats 키가 유실되지 않는지 — 분해하면서 조용히 사라지기 쉬운 부분이다."""
    times, amp = window
    cfg = FeatureConfig()

    ws = extract_window_signal(times, amp, cfg)
    half = features_from_signal(ws.signal, ws.fs_hz, cfg)
    combined = extract_window_features(times, amp, cfg)

    assert set(combined.stats) == set(ws.stats) | set(half.stats)
    # 분해 이전 구현이 내보내던 7키가 모두 있어야 한다
    for key in (
        "selected_pc_indices",
        "candidate_pc_count",
        "selected_pc_count",
        "input_frames",
        "input_subcarriers",
        "selected_subcarrier_count",
        "selected_stream_count",
    ):
        assert key in combined.stats, key


def test_window_samples_survives_composition(window):
    """`features_from_signal` 은 len(signal) 로 채우지만, 합성 시에는 엣지 값이 이겨야 한다."""
    times, amp = window
    cfg = FeatureConfig()
    ws = extract_window_signal(times, amp, cfg)
    combined = extract_window_features(times, amp, cfg)

    assert combined.window_samples == ws.window_samples
    assert ws.window_samples == int(round(cfg.window_seconds * ws.fs_hz))


def test_signal_is_the_uplink_payload_shape(window):
    """엣지가 실제로 올리는 것 — 1-D float32, 3초 × fs."""
    times, amp = window
    ws = extract_window_signal(times, amp)

    assert ws.signal.ndim == 1
    assert ws.signal.dtype == np.float32
    assert ws.signal.shape == (ws.window_samples,)
    assert ws.window_span_s == pytest.approx(float(times[-1] - times[0]))


def test_signal_is_two_orders_smaller_than_tensors(window):
    """D1 의 근거 수치가 유지되는지 — 텐서 233KB 대 신호 약 2KB."""
    times, amp = window
    ws = extract_window_signal(times, amp)
    feats = features_from_signal(ws.signal, ws.fs_hz)

    signal_bytes = ws.signal.nbytes
    tensor_bytes = feats.s3.nbytes + feats.acf.nbytes

    assert tensor_bytes == 233_472, "모델 계약(224x224 + 1x128x64 float32)이 바뀌었다"
    assert signal_bytes < tensor_bytes / 100, f"{signal_bytes}B vs {tensor_bytes}B"


def test_signal_roundtrips_through_wire_encoding(window):
    """엣지가 인코딩하고 클라우드가 디코딩해도 같은 텐서가 나와야 한다.

    계약(`wifiguard_contracts.mqtt`)이 설치돼 있을 때만 돈다. Pi 최소 설치에서는 건너뛴다.
    """
    mqtt = pytest.importorskip("wifiguard_contracts.mqtt")
    times, amp = window

    ws = extract_window_signal(times, amp)
    decoded = mqtt.decode_signal(mqtt.encode_signal(ws.signal), len(ws.signal))

    assert np.array_equal(decoded, ws.signal), "float32 무손실이어야 한다"
    assert np.array_equal(
        features_from_signal(decoded, ws.fs_hz).s3,
        features_from_signal(ws.signal, ws.fs_hz).s3,
    )
