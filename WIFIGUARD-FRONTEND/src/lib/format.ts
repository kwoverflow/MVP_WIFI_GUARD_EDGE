/**
 * 표시 포매터 — mock-store.ts 에서 분리. 서버 시각은 ISO 문자열, 목업은 epoch ms 라 둘 다 받는다.
 * null 은 "감지 미동작"(낙상 감지가 동작하지 않음)으로 표기한다 — 낙상 없음과 구분해야 하는 안전 요구.
 */
import type { Presence, StateMachine } from "@/lib/domain";

export type TimeLike = number | string | Date | null | undefined;

export const DETECTION_INACTIVE_LABEL = "감지 미동작";

export function toDate(ts: TimeLike): Date | null {
  if (ts === null || ts === undefined || ts === "") return null;
  const d = ts instanceof Date ? ts : new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function toEpochMs(ts: TimeLike): number | null {
  return toDate(ts)?.getTime() ?? null;
}

export function fmtTime(ts: TimeLike) {
  const d = toDate(ts);
  return d ? d.toLocaleTimeString("ko-KR", { hour12: false }) : "—";
}

export function fmtDateTime(ts: TimeLike) {
  const d = toDate(ts);
  return d ? d.toLocaleString("ko-KR", { hour12: false }) : "—";
}

export function stateLabel(s: StateMachine | null | undefined) {
  if (!s) return DETECTION_INACTIVE_LABEL;
  return { IDLE: "대기", SUSPECT: "의심", FALL: "낙상", COOLDOWN: "냉각중" }[s];
}

export function stateColor(s: StateMachine | null | undefined) {
  if (!s) return "text-muted";
  return {
    IDLE: "text-muted",
    SUSPECT: "text-warning",
    FALL: "text-primary",
    COOLDOWN: "text-sky-600",
  }[s];
}

export function presenceLabel(p: Presence | null | undefined) {
  if (!p) return DETECTION_INACTIVE_LABEL;
  return p === "PRESENT" ? "재실" : "퇴실";
}

export function presenceColor(p: Presence | null | undefined) {
  if (!p) return "text-muted";
  return p === "PRESENT" ? "text-success" : "text-muted";
}

/** 통합 현재상태: 낙상/의심/COOLDOWN > 재실 > 대기(퇴실). 둘 다 null 이면 감지 미동작. */
export function unifiedStatusLabel(
  s: StateMachine | null | undefined,
  p: Presence | null | undefined,
) {
  if (s === "FALL" || s === "SUSPECT" || s === "COOLDOWN") return stateLabel(s);
  if (!p) return DETECTION_INACTIVE_LABEL;
  return p === "PRESENT" ? "재실" : "퇴실";
}

export function unifiedStatusColor(
  s: StateMachine | null | undefined,
  p: Presence | null | undefined,
) {
  if (s === "FALL" || s === "SUSPECT" || s === "COOLDOWN") return stateColor(s);
  if (!p) return "text-muted";
  return p === "PRESENT" ? "text-success" : "text-muted";
}
