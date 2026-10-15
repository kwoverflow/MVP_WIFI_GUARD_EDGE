"""엣지는 `ssqueezepy`·`numba` 없이 동작해야 한다 (D1의 의존성 귀결).

CWT 가 클라우드로 넘어갔으므로 Pi 설치 집합에서 두 패키지를 뺐다. 그 분리가 실제로
성립하지 않으면 `pyproject.toml` 의 extras 구분이 거짓말이 되고, Pi(ARM64)에서
numba/llvmlite 휠을 구하지 못해 배포가 막힌다.

`sys.modules` 를 지우고 import 를 가로채 "설치되지 않은 상태"를 흉내 낸다.
"""

from __future__ import annotations

import builtins
import importlib
import sys

import numpy as np
import pytest

BLOCKED = ("ssqueezepy", "numba", "llvmlite")

#: 엣지가 기동 시 실제로 import 하는 모듈들. 하나라도 CWT 를 최상단에서 끌어오면 실패한다.
EDGE_MODULES = (
    "wifiguard_edge.features",
    "wifiguard_edge.config",
    "wifiguard_edge.gating",
    "wifiguard_edge.feature_loop",
    "wifiguard_edge.presence_loop",
    "wifiguard_edge.transport.base",
    "wifiguard_edge.transport.replay_source",
    "wifiguard_edge.__main__",
)


@pytest.fixture
def without_cwt(monkeypatch):
    """`ssqueezepy`·`numba` 가 설치되지 않은 환경을 흉내 낸다."""
    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {name.split('.')[0]!r} (테스트가 차단)")
        return real_import(name, *args, **kwargs)

    for mod in list(sys.modules):
        if mod.split(".")[0] in BLOCKED or mod.startswith("wifiguard_edge"):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", guard)
    yield


def _window(fs_hz: float = 166.75, seconds: float = 3.4, subcarriers: int = 245):
    rng = np.random.default_rng(0)
    n = int(seconds * fs_hz)
    times = np.arange(n, dtype=np.float64) / fs_hz
    amps = (
        20.0
        + 4.0 * np.sin(2 * np.pi * 1.7 * times)[:, None]
        + rng.normal(0, 0.5, size=(n, subcarriers))
    ).astype(np.float32)
    return times, amps


@pytest.mark.parametrize("module", EDGE_MODULES)
def test_edge_modules_import_without_cwt(without_cwt, module):
    importlib.import_module(module)


def test_edge_signal_path_works_without_cwt(without_cwt):
    """엣지의 30채널 창과 호환용 1-D 신호 생성은 CWT 패키지 없이 끝나야 한다."""
    features = importlib.import_module("wifiguard_edge.features")
    ws = features.extract_window_signal(*_window())

    assert ws.signal.ndim == 1
    assert ws.signal.dtype == np.float32
    assert ws.signal.nbytes < 4096, "업링크가 약 2KB 라는 전제"
    assert ws.amplitude.ndim == 2
    assert ws.amplitude.shape[1] == 30
    assert ws.amplitude.dtype == np.float32


def test_require_exact_cwt_blocks_startup_without_ssqueezepy(without_cwt):
    """클라우드 기동 가드 — 없으면 조용한 근사 대신 즉시 실패해야 한다."""
    features = importlib.import_module("wifiguard_edge.features")
    with pytest.raises(RuntimeError, match="ssqueezepy"):
        features.require_exact_cwt()


def test_require_exact_cwt_passes_when_installed():
    """개발 PC·클라우드처럼 설치된 환경에서는 통과해야 한다."""
    pytest.importorskip("ssqueezepy")
    from wifiguard_edge.features import require_exact_cwt

    require_exact_cwt()


def test_fallback_produces_different_features_than_ssqueezepy():
    """폴백을 막는 이유의 근거 — 같은 신호에서 **다른** S3 가 나온다.

    이 차이가 조용히 흘러가면 모델 정확도만 떨어지고 아무 테스트도 실패하지 않는다.
    """
    pytest.importorskip("ssqueezepy")
    from wifiguard_edge.features import extract_window_signal, features_from_signal
    from wifiguard_edge.features import common

    ws = extract_window_signal(*_window())
    exact = features_from_signal(ws.signal, ws.fs_hz).s3

    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name.split(".")[0] == "ssqueezepy":
            raise ModuleNotFoundError("차단")
        return real_import(name, *args, **kwargs)

    common._SCALE_CACHE.clear()
    builtins.__import__ = guard
    try:
        approx = features_from_signal(ws.signal, ws.fs_hz).s3
    finally:
        builtins.__import__ = real_import
        common._SCALE_CACHE.clear()

    assert not np.allclose(exact, approx), "폴백이 원본과 같다면 가드가 불필요하다"
