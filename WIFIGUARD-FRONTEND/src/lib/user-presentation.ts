type LabelKind = "room" | "resident" | "device" | "account" | "recipient";

const DEMO_LABELS: Record<LabelKind, Record<string, string>> = {
  room: { "모의 시험방": "거실" },
  resident: { "모의 시험 대상": "대상자 1" },
  device: { "PC 모의 ESP32-C5": "거실 센서" },
  account: { "Pi5 PC 모의검증": "사용자" },
  recipient: { "PC 로컬 알림 수신기": "알림 수신자" },
};

/** Presentation aliases only. Never change user records or relabel production data. */
export function userLabel(value: string, kind: LabelKind, demo: boolean) {
  return demo ? (DEMO_LABELS[kind][value] ?? value) : value;
}

export function activityMessage(message: string, demo: boolean) {
  if (!demo) return message;
  if (message.startsWith("로그인: Pi5 PC 모의검증")) return "로그인";
  const command = message.match(
    /^Pi5 명령 접수: (start|stop|recalibrate|confirm-empty-room)$/,
  )?.[1];
  if (command)
    return (
      {
        start: "모니터링 시작 요청",
        stop: "모니터링 중지 요청",
        recalibrate: "빈방 보정 준비",
        "confirm-empty-room": "빈방 보정 시작 요청",
      } as Record<string, string>
    )[command];
  const phase = message.match(/^PC 모의 RAW 기준값 보정: ([a-z_]+) · /)?.[1];
  const phases: Record<string, string> = {
    waiting_empty_room: "빈방 확인 대기",
    leaving: "빈방 보정 준비 중",
    measuring: "빈방 보정 측정 중",
    done: "빈방 보정 완료",
  };
  if (phase && phases[phase]) return phases[phase];
  if (message.startsWith("[PC 모의 RAW 입력]")) return "데모 입력 상태 변경";
  // Preserve unfamiliar errors rather than hiding potentially important information.
  return message;
}

export const ACTIVITY_LEVELS: Record<string, string> = {
  INFO: "안내",
  WARN: "주의",
  ERROR: "오류",
  FALL: "낙상",
};
