"""움직임(MV)/재실(Presence) 감지 — 낙상 DL 모델과 병렬로 도는 독립 신호 파이프라인.

reference/fall_detect/src(구 이동분산 임계값 기반 백엔드)의 재실 감지 구현을 이식했다
(migration.md 참고). detector.py의 DL 낙상 추론과 같은 CSI 스트림을 입력으로 쓰지만
계산은 완전히 별개다.
"""

from .config import PresenceConfig
from .state_machine import PresenceDetector, PresenceState, PresenceStatus
from .streaming_features import FinalSignalResult, compute_final_signal

__all__ = [
    "PresenceConfig",
    "PresenceDetector",
    "PresenceState",
    "PresenceStatus",
    "FinalSignalResult",
    "compute_final_signal",
]
