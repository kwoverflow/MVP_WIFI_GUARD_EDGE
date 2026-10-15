"""재실(Presence) 감지 상태머신 — 낙상 DL 모델과 무관한 독립 파이프라인.

PRESENT/ABSENT 판정: MV(이동분산) 또는 WANDER(저주파 Welch PSD 에너지, 캘리브레이션
baseline 대비 비율) 중 하나라도 유효하면 활동 시각을 갱신하고, presence_timeout_s
동안 아무 활동도 없으면 ABSENT로 전이한다. reference/fall_detect/src/
presence_state_machine.py를 그대로 이식했다(migration.md §3 — 외부 의존성 없는 순수
로직이라 수정 없이 복사 가능).
"""

from dataclasses import dataclass
from enum import Enum


class PresenceState(Enum):
    """Presence detection state."""

    PRESENT = "present"
    ABSENT = "absent"


@dataclass
class PresenceStatus:
    """Status output from a detector update."""

    state: PresenceState
    mv_current: float
    wander_current: float
    mv_threshold: float
    wander_baseline: float
    wander_ratio_threshold: float
    wander_ratio: float
    wander_confirmed: bool
    last_activity_at: float | None
    seconds_since_activity: float
    just_changed: bool


class PresenceDetector:
    """
    Online presence detection state machine.

    Thresholds live as instance attributes, updated via set_thresholds() (e.g. from
    onboarding calibration), not passed into update() each call.

    The wander signal is compared as a ratio against a calibrated baseline
    (wander_current / wander_baseline >= wander_ratio_threshold), rather than an
    absolute value -- wander_baseline is a Welch PSD band-energy value captured from
    a quiet room during onboarding calibration.

    Wander gets a min-duration debounce (wander_min_duration_s): the live wander
    window (6s, single-segment Welch, no averaging) has coarse frequency resolution
    and high variance, so a single transient burst of low-frequency noise (a door
    opening, a gust of wind) can spike wander_ratio for a tick or two. MV doesn't
    need this -- it's already a smoothed time-domain moving-variance metric, not a
    raw single-tick spectral estimate.
    """

    def __init__(
        self,
        mv_threshold: float = 2.5,
        wander_baseline: float = 0.5,
        wander_ratio_threshold: float = 2.0,
        wander_min_duration_s: float = 2.0,
        presence_timeout_s: float = 6.0,
    ):
        """
        Args:
            mv_threshold: Moving-variance threshold for the MV (motion) signal.
            wander_baseline: Calibrated quiet-room Welch PSD band energy for the wander signal.
            wander_ratio_threshold: Multiplier over wander_baseline that triggers WANDER.
            wander_min_duration_s: Seconds wander_ratio must stay >= wander_ratio_threshold,
                uninterrupted, before it counts as activity (rejects transient spikes).
            presence_timeout_s: Seconds of no activity before flipping to ABSENT.
        """
        self.mv_threshold = mv_threshold
        self.wander_baseline = wander_baseline
        self.wander_ratio_threshold = wander_ratio_threshold
        self.wander_min_duration_s = wander_min_duration_s
        self.presence_timeout_s = presence_timeout_s

        self.state = PresenceState.ABSENT  # boot default: no activity observed yet
        self.last_activity_at: float | None = None
        self._wander_confirm_start_s: float | None = None
        self._mv_history: list[float] = []

    def update(self, now_s: float, mv_value: float, wander_value: float) -> PresenceStatus:
        """
        Update the state machine with the current MV and wander values.

        Args:
            now_s: Current time in seconds (absolute, e.g. from time.time()).
            mv_value: Current moving-variance scalar (motion signal).
            wander_value: Current wander Welch PSD band-energy scalar (low-band signal).

        Returns:
            PresenceStatus reflecting the new state.
        """
        self._mv_history.append(mv_value)
        if len(self._mv_history) > 3:
            self._mv_history.pop(0)

        if len(self._mv_history) == 3:
            smoothed_mv = 0.4 * self._mv_history[2] + 0.3 * self._mv_history[1] + 0.3 * self._mv_history[0]
        elif len(self._mv_history) == 2:
            smoothed_mv = 0.6 * self._mv_history[1] + 0.4 * self._mv_history[0]
        else:
            smoothed_mv = self._mv_history[0]

        wander_ratio = wander_value / self.wander_baseline if self.wander_baseline > 1e-8 else 0.0
        raw_wander_active = wander_ratio >= self.wander_ratio_threshold

        if raw_wander_active:
            if self._wander_confirm_start_s is None:
                self._wander_confirm_start_s = now_s
            wander_confirmed = (now_s - self._wander_confirm_start_s) >= self.wander_min_duration_s
        else:
            self._wander_confirm_start_s = None
            wander_confirmed = False

        if smoothed_mv >= self.mv_threshold:
            self.last_activity_at = now_s
        elif self.state == PresenceState.PRESENT and wander_confirmed:
            self.last_activity_at = now_s

        prev_state = self.state
        active = (
            self.last_activity_at is not None
            and (now_s - self.last_activity_at) < self.presence_timeout_s
        )
        self.state = PresenceState.PRESENT if active else PresenceState.ABSENT

        seconds_since = (
            now_s - self.last_activity_at if self.last_activity_at is not None else float("inf")
        )

        return PresenceStatus(
            state=self.state,
            mv_current=smoothed_mv,
            wander_current=wander_value,
            mv_threshold=self.mv_threshold,
            wander_baseline=self.wander_baseline,
            wander_ratio_threshold=self.wander_ratio_threshold,
            wander_ratio=wander_ratio,
            wander_confirmed=wander_confirmed,
            last_activity_at=self.last_activity_at,
            seconds_since_activity=seconds_since,
            just_changed=(self.state != prev_state),
        )

    def set_thresholds(
        self,
        mv_threshold: float,
        wander_baseline: float,
        wander_ratio_threshold: float,
        wander_min_duration_s: float,
    ):
        """Update thresholds (e.g. from onboarding calibration or a live config endpoint)."""
        self.mv_threshold = mv_threshold
        self.wander_baseline = wander_baseline
        self.wander_ratio_threshold = wander_ratio_threshold
        self.wander_min_duration_s = wander_min_duration_s
