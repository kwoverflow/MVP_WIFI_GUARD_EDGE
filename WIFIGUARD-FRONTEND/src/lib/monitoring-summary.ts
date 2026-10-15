import type { DeviceLive } from "@/api/realtime/types";
import type { DetectionStatus, Resident } from "@/lib/domain";

export type SummaryTone = "neutral" | "success" | "warning" | "danger";

/** Presentation only: missing/disconnected evidence must never look like a safe room. */
export function monitoringSummary(
  live: DeviceLive | null,
  resident: Resident | undefined,
  detection: DetectionStatus,
  mock = false,
) {
  const usable = detection !== "OFFLINE" && live?.online === true;
  const presence = usable ? live.presence?.state : mock ? resident?.presence?.toLowerCase() : null;
  const fall = usable ? live.fall?.detect_state : mock ? resident?.state : null;
  return {
    connected: usable || (mock && resident?.online === true),
    presence: presence === "present" ? "재실" : presence === "absent" ? "퇴실" : "판단 불가",
    presenceDetail:
      presence === "present"
        ? "공간에서 재실이 확인되었습니다"
        : presence === "absent"
          ? "최근 활동이 감지되지 않았습니다"
          : "센서 연결을 확인해 주세요",
    presenceTone: (presence === "present" ? "success" : "neutral") as SummaryTone,
    fall:
      fall === "FALL"
        ? "낙상 감지"
        : fall === "SUSPECT"
          ? "낙상 의심"
          : fall === "COOLDOWN"
            ? "재감지 대기"
            : fall === "IDLE"
              ? "감지 중"
              : usable
                ? "감지 대기"
                : "감지 중지",
    fallDetail:
      fall === "FALL"
        ? "대상자의 상태를 확인해 주세요"
        : fall === "SUSPECT"
          ? "추가 판정을 진행하고 있습니다"
          : fall === "COOLDOWN"
            ? "낙상 감지 후 재감지를 기다립니다"
            : fall === "IDLE"
              ? "확정된 낙상 알림이 없습니다"
              : usable
                ? "감지 결과를 기다리고 있습니다"
                : "센서 연결 후 감지할 수 있습니다",
    fallTone: (fall === "FALL"
      ? "danger"
      : fall === "SUSPECT"
        ? "warning"
        : "neutral") as SummaryTone,
  };
}

export function resourceValue(value: number | null | undefined, usable: boolean, suffix: string) {
  return usable && value != null && Number.isFinite(value)
    ? `${value.toFixed(1)}${suffix}`
    : "미측정";
}
