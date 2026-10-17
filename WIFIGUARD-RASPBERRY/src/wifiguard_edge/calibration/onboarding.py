"""온보딩 3단계 — 캘리브레이션 오케스트레이션.

reference/fall_detect/src/onboarding.py를 이식했다(migration.md §3). 원본은 온보딩
1-2단계(서비스 유형/장치 이름 입력)까지 함께 관리하는 `OnboardingState`를 두지만, 이
백엔드에서는 그 두 단계를 이미 프론트엔드 mock-store가 처리하고 있어(계정/장치
이름은 client 상태) 백엔드가 다시 들고 있을 필요가 없다 — 그래서 캘리브레이션
진행상황만 담는 `CalibrationState`만 이식하고, 이를 감싸던 `OnboardingState`는
가져오지 않았다.

`PipelineConfig` 임포트는 `presence.PresenceConfig`로 교체했고, 그에 맞춰
`mv_threshold`/`calib.mv_threshold`도 `presence_mv_threshold`로 이름을 맞췄다(DL
낙상 확률 임계값과 이름이 겹치지 않도록 — backend/presence/config.py 참고).
"""

import asyncio
import time
from dataclasses import dataclass

import numpy as np

from ..presence import PresenceConfig, compute_final_signal


@dataclass
class CalibrationState:
    """Progress/result of the onboarding step-3 calibration run."""

    phase: str = "idle"  # idle | leaving | waiting_ack | waiting_agc | measuring | done | error
    started_at: float | None = None
    phase_started_at: float | None = None  # when the current phase began; reset on every transition
    agc_duration_s: float | None = None  # how long waiting_agc actually took (observability, not a success check)
    presence_mv_threshold: float | None = None
    wander_baseline: float | None = None
    error: str | None = None


def _remove_outliers_iqr(values: np.ndarray, k: float = 1.5) -> np.ndarray:
    """IQR(Tukey's fence) 기반 이상치 제거.

    캘리브레이션 중 "조용한 방" 구간에도 일시적 스파이크(문 닫힘, 짧은 움직임,
    센서 노이즈 등)가 섞일 수 있는데, 이를 그대로 mean/std에 반영하면 임계값이
    실제 정상 소음 수준보다 과도하게 높게(둔감하게) 잡힌다. 제거 후 표본이 절반
    미만으로 줄어들면(=대부분이 이상치로 판정 = IQR 자체가 신뢰할 수 없는 상황)
    원본을 그대로 반환한다.
    """
    if values.size < 4:
        return values
    q1, q3 = np.percentile(values, [25, 75])
    iqr = q3 - q1
    if iqr <= 0:
        return values
    lo, hi = q1 - k * iqr, q3 + k * iqr
    filtered = values[(values >= lo) & (values <= hi)]
    return filtered if filtered.size >= max(4, values.size // 2) else values


def derive_threshold(values: np.ndarray, k: float, floor: float) -> float:
    """
    Derive a detection threshold from a baseline (quiet-room) signal sample.

    threshold = mean + k*std (이상치 제거 후), clamped to a minimum floor so a
    perfectly quiet baseline (near-zero std) doesn't produce a degenerate
    near-zero threshold that would trigger on any tiny noise.
    """
    values = _remove_outliers_iqr(values)
    return float(max(np.mean(values) + k * np.std(values), floor))


def _compute_baseline_thresholds(
    window: tuple[np.ndarray, np.ndarray],
    cfg: PresenceConfig,
    k_mv: float,
    mv_floor: float,
    wander_baseline_floor: float,
) -> tuple[float, float]:
    """Run the MV and wander signal chains over a captured baseline window and
    derive a threshold/baseline from each. Runs in a thread-pool executor
    (numpy/scipy work).

    MV gets a mean+k*std threshold from its moving_variance time series. Wander is
    different: a Welch PSD band-energy value is a single scalar per window (no time
    series to average), so wander_baseline is just that scalar directly, clamped to
    a floor to avoid a near-zero baseline blowing up the wander_current/
    wander_baseline ratio at runtime.

    window's timestamps must already be in microseconds (see compute_final_signal).
    """
    ts, amp = window

    mv_result = compute_final_signal(
        ts,
        amp,
        window_sec=cfg.window_sec,
        stride_sec=cfg.stride_sec,
        fs_hz=cfg.fs_hz,
        omega=cfg.omega,
        n_streams=cfg.n_streams,
        bandpass_low=cfg.bandpass_low,
        bandpass_high=cfg.bandpass_high,
        bandpass_order=cfg.bandpass_order,
    )
    wander_result = compute_final_signal(
        ts,
        amp,
        window_sec=cfg.wander_window_sec,
        stride_sec=cfg.stride_sec,
        fs_hz=cfg.fs_hz,
        omega=cfg.wander_omega,
        n_streams=cfg.n_streams,
        bandpass_low=cfg.wander_prefilter_low,
        bandpass_high=cfg.wander_prefilter_high,
        bandpass_order=cfg.bandpass_order,
        compute_band_energy=True,
        energy_band_low=cfg.wander_bandpass_low,
        energy_band_high=cfg.wander_bandpass_high,
    )

    presence_mv_threshold = (
        derive_threshold(mv_result.moving_variance, k_mv, mv_floor)
        if mv_result is not None
        else mv_floor
    )
    wander_baseline = (
        max(wander_result.band_energy, wander_baseline_floor)
        if wander_result is not None and wander_result.band_energy is not None
        else wander_baseline_floor
    )
    return presence_mv_threshold, wander_baseline


async def run_calibration(
    monitor,
    cfg: PresenceConfig,
    calib: CalibrationState,
    executor,
    *,
    leave_wait_s: float = 30.0,
    silence_confirm_s: float = 0.2,
    silence_timeout_s: float = 3.0,
    resume_timeout_s: float = 20.0,
    baseline_window_s: float = 30.0,
    poll_interval_s: float = 0.05,
    k_mv: float = 2.0,
    mv_floor: float = 0.3,
    wander_baseline_floor: float = 0.05,
) -> None:
    """
    Drive onboarding step 3: wait leave_wait_s for the installer to vacate the
    room, send "train", wait for the firmware's AGC-settle window to elapse,
    then capture a baseline_window_s CSI window (expected to be an empty/quiet
    room) and derive presence_mv_threshold/wander_baseline from it.

    leave_wait_s defaults to 30s -- gives the installer time to physically
    leave the room before anything (train command, AGC, baseline capture)
    begins. Runs before send_line("train") so no measurement starts early.

    baseline_window_s defaults to 30s (longer than the live 10s wander window) --
    calibration is one-shot so responsiveness doesn't matter, and a longer
    capture gives a more stable Welch PSD baseline estimate. Total run time is
    ~61s (leave_wait_s + ~0.2s ack + ~1s AGC + baseline_window_s) -- this is
    deliberately the real, uncompressed timing (see docs 기능명세서 conflict
    note in the migration plan); the frontend shows this actual phase/timing
    rather than a simplified placeholder.

    silence_confirm_s defaults to 0.2s -- comfortably above the ~3.3ms it takes
    in-flight UART bytes to drain at 2 Mbaud (one CSI frame is ~660 bytes; it
    was ~7.2ms back when the link ran at 921600), while leaving ~0.8s of margin
    under the firmware's ~1s AGC-settle window (csi_recv_calibrate's
    CSI_TRAIN_DURATION_US). A prior 0.7s default left only ~0.3s of margin,
    risking an intermittent waiting_ack timeout if scheduling jitter pushed
    past it. The 2026-09-10 baud change only widened this margin, so the 0.2s
    default still holds -- it is bounded by the AGC window, not by drain time.

    k_mv defaults to 2.0 -- deliberately tuned for sensitivity over precision as
    an interim measure; expect more false positives until this is retuned
    against real usage data.

    Mutates calib in place as it progresses so GET /onboarding/calibrate/status
    can report live progress: calib.phase_started_at resets on every phase
    transition (unlike calib.started_at, which is fixed at the very start), so
    callers can compute per-phase elapsed time. calib.agc_duration_s records
    how long the waiting_agc phase actually took, once known -- an
    observability signal only (whether the timing looks sane), not proof the
    firmware's AGC calibration itself succeeded; the firmware has no protocol
    to report that.

    All durations/k-constants are parameters (not module globals) so tests
    can pass small values and run in milliseconds.
    """
    calib.phase = "idle"
    calib.error = None
    calib.presence_mv_threshold = None
    calib.wander_baseline = None
    calib.agc_duration_s = None
    calib.started_at = time.time()

    try:
        if not monitor.running:
            calib.phase = "error"
            calib.error = "device not connected"
            return

        # Phase 0: leave-room wait -- happens before send_line("train") so no
        # measurement of any kind starts until the installer has had time to leave.
        calib.phase = "leaving"
        calib.phase_started_at = time.time()
        await asyncio.sleep(leave_wait_s)
        if not monitor.running:
            calib.phase = "error"
            calib.error = "device disconnected during calibration"
            return

        if not monitor.send_line("train"):
            calib.phase = "error"
            calib.error = "failed to send train command to device"
            return

        # Phase A: confirm the command landed by waiting for in-flight frames to
        # drain and packet flow to actually go quiet (the firmware sends nothing
        # while training).
        calib.phase = "waiting_ack"
        calib.phase_started_at = time.time()
        phase_start = time.time()
        last_count = monitor.packet_count
        last_change_at = phase_start
        while True:
            if not monitor.running:
                calib.phase = "error"
                calib.error = "device disconnected during calibration"
                return
            await asyncio.sleep(poll_interval_s)
            now = time.time()
            count = monitor.packet_count
            if count != last_count:
                last_count = count
                last_change_at = now
            elif now - last_change_at >= silence_confirm_s:
                break
            if now - phase_start >= silence_timeout_s:
                calib.phase = "error"
                calib.error = "device did not stop streaming after train command (command may not have been received)"
                return

        # Phase B: wait for the firmware to finish its AGC-settle window and
        # resume streaming.
        calib.phase = "waiting_agc"
        calib.phase_started_at = time.time()
        phase_start = time.time()
        baseline_count = monitor.packet_count
        while True:
            if not monitor.running:
                calib.phase = "error"
                calib.error = "device disconnected during calibration"
                return
            await asyncio.sleep(poll_interval_s)
            if monitor.packet_count > baseline_count:
                break
            if time.time() - phase_start >= resume_timeout_s:
                calib.phase = "error"
                calib.error = "streaming did not resume after AGC calibration window (timeout)"
                return

        # How long the AGC-settle window actually took -- an observability signal
        # (does the timing look sane?), not proof the firmware's AGC calibration
        # itself succeeded; the firmware has no protocol to report that.
        calib.agc_duration_s = time.time() - phase_start

        # Phase C: capture the baseline window. Streaming just resumed, so once
        # baseline_window_s elapses, every sample in the trailing window is
        # guaranteed post-resume (pre-training samples are older than that).
        calib.phase = "measuring"
        calib.phase_started_at = time.time()
        await asyncio.sleep(baseline_window_s)
        if not monitor.running:
            calib.phase = "error"
            calib.error = "device disconnected during calibration"
            return

        window = monitor.get_window(baseline_window_s)
        if window is None:
            calib.phase = "error"
            calib.error = "insufficient CSI data captured for baseline"
            return

        # backend.csi.buffer.RingBuffer.get_window() returns seconds; the ported
        # signal chain (compute_final_signal / resample_signal) expects microseconds.
        times_s, amp = window
        window_us = (times_s * 1e6, amp)

        loop = asyncio.get_event_loop()
        try:
            presence_mv_threshold, wander_baseline = await loop.run_in_executor(
                executor,
                _compute_baseline_thresholds,
                window_us,
                cfg,
                k_mv,
                mv_floor,
                wander_baseline_floor,
            )
        except Exception as exc:
            calib.phase = "error"
            calib.error = f"baseline computation failed: {exc}"
            return

        cfg.presence_mv_threshold = presence_mv_threshold
        cfg.wander_baseline = wander_baseline
        calib.presence_mv_threshold = presence_mv_threshold
        calib.wander_baseline = wander_baseline
        calib.phase = "done"
        calib.phase_started_at = time.time()
    except Exception as exc:
        # Safety net for anything not covered by the explicit checks above --
        # without this, an unanticipated exception (e.g. a duck-typing mismatch
        # on `monitor`) leaves calib.phase stuck at a non-terminal value
        # forever, permanently blocking retries via the phase-not-in
        # ("idle","done","error") guard on /onboarding/calibrate/start.
        calib.phase = "error"
        calib.error = f"calibration failed unexpectedly: {exc}"
