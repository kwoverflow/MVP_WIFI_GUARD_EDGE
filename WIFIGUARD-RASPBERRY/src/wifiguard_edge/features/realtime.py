"""실시간 3초 윈도우를 학습과 동일한 30채널 진폭창으로 만든다.

오프라인 배치 스크립트 build_inhouse_native_hz_midpoint_s3_acf.py 의
compute_native_features 경로를 링버퍼 입력에 맞게 감싼 것이다.
파이프라인: 균일 그리드 리샘플 -> 스트림 선택 -> PCA motion signal
          -> S3 scalogram (224,224) + PCA-ACF (1,128,64)

## 함수 3개로 나뉘어 있다 (2026-09-10, D1)

    extract_window_signal(times, amplitude, cfg) -> WindowSignal      ← 엣지(Pi) 운영 경로
    features_from_signal(signal, fs_hz, cfg)     -> WindowFeatures    ← 구 모델 회귀·진단 경로
    extract_window_features(times, amplitude, cfg) -> WindowFeatures  ← 구 경로 합성

현재 temporal segmentation 모델은 30채널 진폭창 전체에서 S3·PCA-ACF 피처를
계산하므로 1-D 대표신호만으로는 입력을 복원할 수 없다. 운영 메시지는 학습
고정 규칙으로 선택·리샘플한 `WindowSignal.amplitude`(`T x 30`)을 싣는다.
`signal`은 구 계약과 진단 호환을 위해 함께 유지한다.

`extract_window_features` 의 시그니처와 출력은 **바뀌지 않았다.** 벤치·회귀 대조가 이 함수를
쓰고, 클라우드는 `features_from_signal` 을 쓴다. 둘의 동등성은
`tests/test_feature_split.py` 가 배열 비트 단위로 단언한다.

클라우드 피처와 학습·추론 계약은 `services/csi-fall-segmentation`이 담당한다.
이 모듈은 Pi의 서브캐리어 선택·리샘플 계약과 구 모델 회귀 경로를 보존한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .acf import compute_pca_acf
from .common import compute_s3_scalogram, select_pc_signal, select_streams


@dataclass(frozen=True)
class FeatureConfig:
    """모델 호환 파라미터. 학습 시 설정과 일치해야 한다 (README_KO.md 표 참조)."""

    window_seconds: float = 3.0
    target_subcarriers: int = 30
    # 수집 CSV는 raw 245쌍에서 121,122를 제외하고 30개를 균등 선택했다.
    train_raw_pairs: int = 245
    train_drop_pair_indices: tuple[int, ...] = (121, 122)
    omega: int = 30
    moving_variance_radius_seconds: float = 0.4
    freq_min_hz: float = 1.0
    freq_max_hz: float = 170.0
    image_size: int = 224
    th_scmax: float = 1.0
    kappa: float = 1.0
    carrier_hz: float = 2.4e9
    acf_lag_seconds: float = 0.4
    acf_lag_output_bins: int = 128
    acf_time_bins: int = 64
    acf_clip_percentile: float = 99.5
    # 측정 fs 양자화 간격. 윈도우마다 미세하게 다른 측정값을 격자에 스냅해
    # CWT scale 캐시가 적중하게 한다. 오프라인 파이프라인도 recording 전체
    # 중앙값 하나를 쓰므로 per-window 지터 흡수는 의미상 동일하다.
    fs_quantize_hz: float = 0.25


@dataclass
class WindowSignal:
    """엣지가 클라우드로 올리는 것 — 선택·리샘플된 30채널 창과 호환용 1-D 신호.

    `amplitude`가 temporal segmentation 운영 입력이고 `signal`은 구 계약·진단
    호환용이다.
    """

    signal: np.ndarray  # (window_samples,) float32
    amplitude: np.ndarray  # (window_samples, selected_subcarrier_count) float32
    fs_hz: float
    window_samples: int
    window_span_s: float
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class WindowFeatures:
    s3: np.ndarray  # (224, 224) float32
    acf: np.ndarray  # (1, 128, 64) float32
    fs_hz: float
    window_samples: int
    stats: dict[str, Any] = field(default_factory=dict)


def select_subcarrier_indices(n_available: int, config: FeatureConfig) -> np.ndarray:
    """수신 프레임의 서브캐리어 수에 맞춰 학습과 동일한 선택 규칙을 적용한다.

    학습 수집과 같은 245쌍이면 동일 인덱스가 나온다. 다른 수라면 동일한
    균등 선택 규칙을 그대로 적용하되 stats에 남겨 확인할 수 있게 한다.
    """
    dropped = set(config.train_drop_pair_indices) if n_available == config.train_raw_pairs else set()
    candidates = np.asarray([idx for idx in range(n_available) if idx not in dropped], dtype=np.int32)
    target = min(config.target_subcarriers, len(candidates))
    if target <= 0:
        raise ValueError(f"no subcarriers available (n_available={n_available})")
    positions = np.round(np.linspace(0, len(candidates) - 1, target)).astype(np.int32)
    return candidates[np.unique(positions)]


def measure_native_fs(times: np.ndarray) -> float:
    diffs = np.diff(times)
    positive = diffs[diffs > 0]
    if len(positive) == 0:
        return float("nan")
    return float(1.0 / np.median(positive))


def resample_uniform(times: np.ndarray, amplitude: np.ndarray, fs_hz: float) -> np.ndarray:
    """타임스탬프 기준 균일 그리드 선형 보간 (오프라인 resample_recording과 동일)."""
    grid_step = 1.0 / fs_hz
    grid_count = int(math.floor((times[-1] - times[0]) / grid_step)) + 1
    grid = times[0] + np.arange(grid_count, dtype=np.float64) * grid_step
    resampled = np.empty((grid_count, amplitude.shape[1]), dtype=np.float32)
    for stream_idx in range(amplitude.shape[1]):
        resampled[:, stream_idx] = np.interp(grid, times, amplitude[:, stream_idx]).astype(np.float32)
    return resampled


def extract_window_signal(
    times: np.ndarray,
    amplitude: np.ndarray,
    config: FeatureConfig | None = None,
) -> WindowSignal:
    """링버퍼 윈도우 → 1-D 합성 대표신호. **엣지(Pi)가 담당하는 구간.**

    times: 단조 증가 초 단위 (unwrap 완료), amplitude: (frames, subcarriers).
    윈도우가 3초에 못 미치거나 프레임이 너무 적으면 ValueError.

    단계: 중복 ts 제거 → fs 측정·양자화 → 서브캐리어 선택(정적 규칙) → 균일 리샘플
          → 3초 절단 → 스트림 선택(q-metric) → PCA 합성.
    여기까지는 numpy 만 쓴다 — CWT(ssqueezepy)는 다음 단계라 Pi 에 설치할 필요가 없다.
    """
    cfg = config or FeatureConfig()
    if times.size < 8:
        raise ValueError(f"too few frames: {times.size}")
    span = float(times[-1] - times[0])
    if span < cfg.window_seconds * 0.9:
        raise ValueError(f"window span {span:.2f}s < required {cfg.window_seconds}s")

    # 중복/역행 타임스탬프 제거 (오프라인 load_recording과 동일한 정리)
    unique_mask = np.ones(len(times), dtype=bool)
    unique_mask[1:] = np.diff(times) > 0
    times = times[unique_mask]
    amplitude = amplitude[unique_mask]

    fs_hz = measure_native_fs(times)
    if not np.isfinite(fs_hz) or fs_hz <= 0:
        raise ValueError(f"invalid native fs {fs_hz}")
    if cfg.fs_quantize_hz > 0:
        fs_hz = max(cfg.fs_quantize_hz, round(fs_hz / cfg.fs_quantize_hz) * cfg.fs_quantize_hz)

    selected = select_subcarrier_indices(amplitude.shape[1], cfg)
    resampled = resample_uniform(times, amplitude[:, selected], fs_hz)

    window_samples = int(round(cfg.window_seconds * fs_hz))
    if len(resampled) < window_samples:
        raise ValueError(f"resampled length {len(resampled)} < window {window_samples}")
    window = resampled[-window_samples:]

    variance_radius = max(1, int(round(cfg.moving_variance_radius_seconds * fs_hz)))
    selected_streams, _stream_q = select_streams(window, omega=cfg.omega, w_radius=variance_radius)
    signal, pc_stats = select_pc_signal(window, selected_streams, w_radius=variance_radius)
    return WindowSignal(
        signal=signal,
        amplitude=window.astype(np.float32, copy=False),
        fs_hz=fs_hz,
        window_samples=window_samples,
        window_span_s=span,
        stats={
            **pc_stats,
            "input_frames": int(times.size),
            "input_subcarriers": int(amplitude.shape[1]),
            "selected_subcarrier_count": int(len(selected)),
            "selected_stream_count": int(len(selected_streams)),
        },
    )


def require_exact_cwt() -> None:
    """`ssqueezepy` 가 실제로 import 되는지 확인한다. **모델서버가 기동 시 호출할 것.**

    없으면 `compute_s3_scalogram` 이 `fallback_cwt`(common.py:177)로 **조용히** 넘어가는데,
    그 함수 docstring 이 스스로 "원본 대비 근사"라고 인정한다. 학습 때와 다른 피처가
    생산되면 테스트는 전부 통과하고 모델 정확도만 떨어진다 — 가장 발견하기 어려운 종류의
    고장이다. 프로덕션 경로에서는 폴백을 허용하지 않고 기동 자체를 막는다.

    폴백은 개발 편의용으로만 남긴다(엣지는 애초에 이 경로를 타지 않는다).
    """
    try:
        import ssqueezepy  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "ssqueezepy 가 없다. compute_s3_scalogram 이 근사 폴백(fallback_cwt)으로 넘어가"
            "학습 때와 다른 S3 스칼로그램을 만들고, 그 차이는 정확도 저하로만 드러난다. "
            "모델서버에는 `pip install 'wifiguard-edge[cwt]'` 로 설치할 것."
        ) from exc


def features_from_signal(
    signal: np.ndarray,
    fs_hz: float,
    config: FeatureConfig | None = None,
) -> WindowFeatures:
    """1-D 대표신호 → 모델 입력 텐서. **클라우드 모델서버가 담당하는 구간.**

    `ssqueezepy` 가 필요하다(CWT). 없어도 예외 없이 동작하지만 그것은 근사 폴백이며
    프로덕션에서 쓰면 안 된다 — 호출자가 기동 시 `require_exact_cwt()` 로 막을 것.
    """
    cfg = config or FeatureConfig()
    s3, cwt_stats = compute_s3_scalogram(
        signal=signal,
        fs_hz=fs_hz,
        freq_min_hz=cfg.freq_min_hz,
        freq_max_hz=cfg.freq_max_hz,
        image_size=cfg.image_size,
        th_scmax=cfg.th_scmax,
        kappa=cfg.kappa,
        carrier_hz=cfg.carrier_hz,
    )
    acf = compute_pca_acf(
        signal,
        fs_hz=fs_hz,
        lag_seconds=cfg.acf_lag_seconds,
        time_bins=cfg.acf_time_bins,
        lag_output_bins=cfg.acf_lag_output_bins,
        clip_percentile=cfg.acf_clip_percentile,
    )
    return WindowFeatures(
        s3=s3.astype(np.float32),
        acf=acf.astype(np.float32),
        fs_hz=fs_hz,
        window_samples=int(len(signal)),
        stats=dict(cwt_stats),
    )


def extract_window_features(
    times: np.ndarray,
    amplitude: np.ndarray,
    config: FeatureConfig | None = None,
) -> WindowFeatures:
    """링버퍼에서 꺼낸 (시각, 진폭) 윈도우를 모델 입력 피처로 변환한다.

    위 두 함수의 합성이다. 시그니처와 출력은 분해 이전과 동일하다 — 벤치와 회귀 대조가
    이 함수를 쓴다. 실제 파이프라인은 두 단계가 엣지와 클라우드로 나뉘어 실행된다.
    """
    cfg = config or FeatureConfig()
    ws = extract_window_signal(times, amplitude, cfg)
    features = features_from_signal(ws.signal, ws.fs_hz, cfg)
    features.window_samples = ws.window_samples
    features.stats = {**ws.stats, **features.stats}
    return features
