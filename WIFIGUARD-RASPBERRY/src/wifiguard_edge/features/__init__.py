"""낙상 모델 입력 피처 체인. 재실감지(`presence/`)와는 완전히 별개 경로다.

    extract_window_signal   링버퍼 윈도우 → 1-D 합성 대표신호   ← 엣지(Pi)
    features_from_signal    대표신호 → S3 + PCA-ACF 텐서        ← 클라우드 모델서버
    extract_window_features 위 둘의 합성 (벤치·회귀 대조용)

이 패키지는 **클라우드 모델서버가 그대로 import 한다**(`wifiguard-serving` 이
`wifiguard-edge` 를 path 의존으로 건다). 복사본을 만들면 갈라져도 테스트는 통과하고
모델 정확도만 조용히 떨어진다 — `common.py` 상단 주석 참조.

`ssqueezepy`/`numba` 는 `features_from_signal` 경로에서만 필요하며 함수 안에서 import 된다.
Pi 는 두 패키지 없이 `extract_window_signal` 까지 돌릴 수 있다.
"""

from .realtime import (
    FeatureConfig,
    WindowFeatures,
    WindowSignal,
    extract_window_features,
    extract_window_signal,
    features_from_signal,
    require_exact_cwt,
)

__all__ = [
    "FeatureConfig",
    "WindowFeatures",
    "WindowSignal",
    "extract_window_features",
    "extract_window_signal",
    "features_from_signal",
    "require_exact_cwt",
]
