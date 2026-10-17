/**
 * 캘리브레이션 데모 타이머 — mock-store.startDeviceReset 의 61초 4단계 진행을 스토어 무의존 함수로 분리.
 *
 * 실시간 경로(엣지 캘리브레이션 ack)가 붙기 전까지 장치 등록/재설정 UI 는 이 타이머로 진행을 보여주고,
 * 완료 값은 PATCH /devices/{id} 로 영속한다 (routes/devices.tsx). 진행 중 새로고침하면 타이머는 사라지고
 * 서버의 calibrationStage 만 남는다 — 수용된 제약.
 */
import {
  CALIBRATION_PHASE_SECONDS,
  CALIBRATION_STAGE_ORDER,
  type CalibrationStage,
} from "@/lib/domain";

export interface CalibrationResult {
  presenceMvThreshold: number;
  wanderBaseline: number;
}

export interface CalibrationDemoHandlers {
  onProgress: (stage: CalibrationStage, progress: number) => void;
  onDone: (result: CalibrationResult) => void;
  /** 기본 100ms */
  tickMs?: number;
  /** 결과 생성기 — 테스트에서 고정값 주입용 */
  makeResult?: () => CalibrationResult;
}

const boundaries = CALIBRATION_STAGE_ORDER.reduce<
  { stage: CalibrationStage; start: number; end: number }[]
>((acc, stage) => {
  const prevEnd = acc.length ? acc[acc.length - 1].end : 0;
  return [
    ...acc,
    { stage, start: prevEnd, end: prevEnd + CALIBRATION_PHASE_SECONDS[stage] * 1000 },
  ];
}, []);

export const CALIBRATION_TOTAL_MS = boundaries[boundaries.length - 1].end; // 61,200ms

export function defaultCalibrationResult(): CalibrationResult {
  return {
    presenceMvThreshold: Number((1.6 + Math.random() * 0.8).toFixed(3)),
    wanderBaseline: Number((0.35 + Math.random() * 0.3).toFixed(3)),
  };
}

/** 진행 중인 데모 타이머 (장치 id → cancel). 같은 장치를 다시 시작하면 이전 타이머는 취소된다. */
const running = new Map<string, () => void>();

export function runCalibrationDemo(deviceId: string, h: CalibrationDemoHandlers): () => void {
  running.get(deviceId)?.();
  const start = Date.now();
  const tickMs = h.tickMs ?? 100;
  h.onProgress("LEAVING", 0);
  const id = setInterval(() => {
    const el = Date.now() - start;
    if (el >= CALIBRATION_TOTAL_MS) {
      cancel();
      h.onDone((h.makeResult ?? defaultCalibrationResult)());
      return;
    }
    const current = boundaries.find((b) => el < b.end) ?? boundaries[boundaries.length - 1];
    h.onProgress(current.stage, (el - current.start) / (current.end - current.start));
  }, tickMs);
  function cancel() {
    clearInterval(id);
    running.delete(deviceId);
  }
  running.set(deviceId, cancel);
  return cancel;
}

export function isCalibrationDemoRunning(deviceId: string): boolean {
  return running.has(deviceId);
}

/** 현재 단계의 남은 초 (UI 표시용). */
export function secondsLeft(stage: CalibrationStage, progress: number): number {
  return Math.max(0, Math.ceil((1 - progress) * (CALIBRATION_PHASE_SECONDS[stage] || 0)));
}
