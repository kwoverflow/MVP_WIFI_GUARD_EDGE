"""단위 규약 고정 — 재실 체인은 마이크로초, 피처 체인은 초.

같은 `RingBuffer` 출력을 두 체인이 서로 다른 단위로 받는다. 변환은 각 루프 안에서만
일어나야 하고 밖으로 새면 안 된다. 1e6 배 차이라 틀려도 예외가 나지 않고 **조용히
잘못된 값**이 나온다는 것이 이 테스트가 있는 이유다 — fs 가 1e6 배로 잘못 측정되면
그냥 이상한 숫자가 흘러갈 뿐이다.

    RingBuffer.get_window()  →  (times_초, amplitude)
        ├─ PresenceLoop      →  compute_final_signal(times*1e6, ...)   마이크로초
        └─ FeatureLoop       →  extract_window_signal(times, ...)      초 그대로
"""

from __future__ import annotations

import numpy as np
import pytest

from wifiguard_edge.csi.buffer import RingBuffer
from wifiguard_edge.csi.protocol import CsiFrame
from wifiguard_edge.features.realtime import extract_window_signal
from wifiguard_edge.presence.streaming_features import compute_final_signal

FS = 166.75
SUB = 245


def fill(ring: RingBuffer, seconds: float = 4.0) -> None:
    rng = np.random.default_rng(0)
    n = int(seconds * FS)
    for i in range(n):
        t = i / FS
        amps = (20.0 + 2.0 * np.sin(2 * np.pi * 1.7 * t) + rng.normal(0, 0.5, size=SUB)).astype(np.float32)
        ring.append(
            CsiFrame(
                seq=i, mac="1a:00:00:00:00:00", rssi=-45, noise_floor=-92, channel=48,
                timestamp_us=int(t * 1e6), fft_gain=0, agc_gain=0,
                csi_len=SUB * 2, amps=amps, host_time=t,
            )
        )


@pytest.fixture(scope="module")
def ring() -> RingBuffer:
    r = RingBuffer(max_seconds=30.0)
    fill(r)
    return r


def test_ring_buffer_emits_seconds(ring):
    """링버퍼는 **초** 를 낸다. 장치 클럭은 마이크로초지만 unwrap 하면서 나눈다."""
    times, _ = ring.get_window(3.5)
    span = float(times[-1] - times[0])
    assert 3.0 < span < 3.6, f"초 단위가 아니다: span={span}"


def test_feature_chain_takes_seconds_directly(ring):
    """`extract_window_signal` 에 1e6 을 곱하면 안 된다 — fs 가 1e6 배로 어긋난다."""
    times, amps = ring.get_window(3.5)

    ok = extract_window_signal(times, amps)
    assert FS * 0.9 < ok.fs_hz < FS * 1.1, f"fs 가 이상하다: {ok.fs_hz}"

    # 잘못된 단위를 넣으면 fs 가 터무니없이 작아진다(주기가 1e6 배로 늘어난 셈).
    wrong = extract_window_signal(times * 1e6, amps)
    assert wrong.fs_hz < 1.0, "단위를 틀려도 조용히 통과한다는 사실 자체를 고정해 둔다"


def test_presence_chain_takes_microseconds(ring):
    """`compute_final_signal` 은 마이크로초를 받는다 — docstring 이 그렇게 못 박고 있다."""
    times, amps = ring.get_window(3.0)

    ok = compute_final_signal(times * 1e6, amps, window_sec=3.0, stride_sec=0.25, fs_hz=100.0, omega=24)
    assert ok is not None
    assert ok.window_duration_s == pytest.approx(float(times[-1] - times[0]), rel=0.05)

    # 초를 그대로 넣으면 관측 구간이 1e6 배로 짧게 해석된다
    wrong = compute_final_signal(times, amps, window_sec=3.0, stride_sec=0.25, fs_hz=100.0, omega=24)
    if wrong is not None:
        assert wrong.window_duration_s < 1e-3


def _count_1e6_multiplications(fn) -> int:
    """`x * 1e6` 형태의 **연산** 개수. 주석·문자열은 세지 않는다."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mult)
        and any(isinstance(side, ast.Constant) and side.value == 1e6 for side in (node.left, node.right))
    )


def test_feature_loop_does_not_convert():
    """FeatureLoop 에는 `* 1e6` 이 없어야 한다 — PresenceLoop 를 베껴 쓰다 생기기 쉬운 실수."""
    from wifiguard_edge.feature_loop import FeatureLoop

    assert _count_1e6_multiplications(FeatureLoop._tick) == 0, "피처 체인은 초를 그대로 쓴다"


def test_presence_loop_does_convert():
    """반대로 PresenceLoop 에는 변환이 남아 있어야 한다 (MV·wander 두 경로)."""
    from wifiguard_edge.presence_loop import PresenceLoop

    assert (_count_1e6_multiplications(PresenceLoop._tick)
            + _count_1e6_multiplications(PresenceLoop._compute_wander_current)) == 2
