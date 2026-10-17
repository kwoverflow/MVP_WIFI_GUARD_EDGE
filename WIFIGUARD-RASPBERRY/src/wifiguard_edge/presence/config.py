"""재실(움직임/Wander) 감지 파이프라인 설정.

reference/fall_detect/src/pipeline.py의 PipelineConfig에서 재실 감지가 실제로 쓰는
필드만 추려 이식했다(migration.md §5). `mv_threshold`는 `presence_mv_threshold`로
개명했다 — detector.py의 DL 낙상 확률 임계값(threshold, 기본 0.468)과는 이름은 물론
척도(0~1 확률 vs 이동분산 스칼라)도 전혀 다른 별도 값이라 이름을 겹치게 두면 혼동이
생긴다. 캘리브레이션(onboarding.py)이 presence_mv_threshold/wander_baseline을
런타임에 갱신하므로 frozen dataclass로 만들지 않는다(backend/features/realtime.py의
FeatureConfig와 달리 mutable).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PresenceConfig:
    # MV(움직임) 신호체인
    window_sec: float = 3.0
    stride_sec: float = 0.25  # backend 감지 루프(detector.STRIDE_SEC)와 동일 — 설명용, 계산에 필수는 아님
    fs_hz: float = 100.0
    mv_window_sec: float = 0.5
    n_streams: int = 10
    bandpass_low: float = 0.5
    bandpass_high: float = 50.0
    bandpass_order: int = 4
    presence_mv_threshold: float = 2.0  # 캘리브레이션으로 갱신됨 (onboarding.run_calibration)

    # Wander(저주파 잔향) 신호체인 — 사전 필터(prefilter)는 넓게, Welch 측정 대역(bandpass)은
    # 좁게 잡아야 한다. 같게 만들면 사전 필터가 이미 전체 에너지를 그 대역에 가둬버려
    # 정규화 후 측정값의 구분력이 사라진다(occupation_pipline.md §2 실측 근거).
    wander_window_sec: float = 10.0
    wander_mv_window_sec: float = 1.0
    wander_prefilter_low: float = 0.05
    wander_prefilter_high: float = 5.0
    wander_bandpass_low: float = 0.1
    wander_bandpass_high: float = 0.5
    wander_baseline: float = 0.5  # 캘리브레이션으로 갱신됨
    wander_ratio_threshold: float = 1.8
    wander_min_duration_s: float = 2.0

    presence_timeout_s: float = 10.0

    @property
    def omega(self) -> int:
        """Derive omega (moving-variance half-width) from mv_window_sec."""
        return max(1, round((self.mv_window_sec * self.fs_hz - 1) / 2))

    @property
    def wander_omega(self) -> int:
        """Derive omega (moving-variance half-width) from wander_mv_window_sec."""
        return max(1, round((self.wander_mv_window_sec * self.fs_hz - 1) / 2))
