/**
 * 전체화면 낙상 알람 상태 — FallAlarmModal 전용 외부 스토어.
 *
 * 알람 경로 이중화 원칙(README '알림 이중 경로')에 따라 앱 내 알람은 별도 경로다.
 * 실시간(/ws/live)이 붙기 전에는 낙상 시뮬레이션 성공 시에만 set 되고, 사용자가 응답하면 clear 된다.
 * 기존 알람이 있으면 새 알람으로 덮어쓰지 않는다 (mock-store `alarm: prev.alarm ?? evt` 동작 유지).
 * VITE_USE_MOCK=1 이면 mock-store 의 alarm 을 그대로 읽는다 (시뮬레이션 tick 이 알람을 만든다).
 */
import { useMemo, useSyncExternalStore } from "react";

import { USE_MOCK } from "@/api/client";
import { toFall } from "@/api/mock/adapters";
import type { FallEvent } from "@/lib/domain";
import { useStore as useMockStore } from "@/lib/mock-store";

let alarm: FallEvent | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export const alarmStore = {
  get: () => alarm,
  /** 이미 표시 중인 알람이 있으면 유지한다. */
  raise(fall: FallEvent) {
    if (alarm) return;
    alarm = fall;
    emit();
  },
  clear() {
    if (!alarm) return;
    alarm = null;
    emit();
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

function useRealAlarm(): FallEvent | null {
  return useSyncExternalStore(alarmStore.subscribe, alarmStore.get, () => null);
}

function useMockAlarm(): FallEvent | null {
  const a = useMockStore((s) => s.alarm);
  return useMemo(() => (a ? toFall(a, { facilityId: null, ownerUserId: null }) : null), [a]);
}

export const useAlarm: () => FallEvent | null =
  import.meta.env.VITE_USE_MOCK === "1" ? useMockAlarm : useRealAlarm;
