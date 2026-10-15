/**
 * 실시간 구독 훅.
 *
 *   useLive()                 스냅샷 전체 (연결 상태 · 전 기기)
 *   useLiveDevice(deviceId)   기기 하나의 최신 상태
 *   useMvSeries(deviceId)     그 기기의 MV 시계열
 *   useDetectionState()       F-W11 3상태 (ACTIVE / INACTIVE / OFFLINE)
 *
 * 목업 모드(`VITE_USE_MOCK=1`)에서는 연결하지 않는다 — 데모는 mock-store 시뮬레이션이
 * 값을 만들고, 그때 WS 까지 붙으면 두 출처가 같은 화면을 다투게 된다.
 */

import { useSyncExternalStore } from "react";

import { USE_MOCK } from "@/api/client";
import type { DetectionStatus } from "@/lib/domain";

import { liveSocket, type LiveSnapshot, type MvPoint } from "./socket";
import type { DeviceLive } from "./types";

const IDLE: LiveSnapshot = {
  status: "idle",
  devices: {},
  history: {},
  deviceIds: [],
  lastError: null,
  droppedFrames: 0,
};

const NO_SUBSCRIBE = () => () => {};
const NO_SNAPSHOT = () => IDLE;

/** 연결 상태와 전 기기 최신값. 마운트된 컴포넌트가 하나라도 있으면 소켓이 열린다. */
export function useLive(): LiveSnapshot {
  return useSyncExternalStore(
    USE_MOCK ? NO_SUBSCRIBE : liveSocket.subscribe,
    USE_MOCK ? NO_SNAPSHOT : liveSocket.getSnapshot,
    liveSocket.getServerSnapshot,
  );
}

/** 기기 하나. 서버가 그 기기 소식을 준 적 없으면 `null` = "감지 미동작". */
export function useLiveDevice(deviceId: string | null | undefined): DeviceLive | null {
  const live = useLive();
  return deviceId ? (live.devices[deviceId] ?? null) : null;
}

const EMPTY_SERIES: MvPoint[] = [];

export function useMvSeries(deviceId: string | null | undefined): MvPoint[] {
  const live = useLive();
  return deviceId ? (live.history[deviceId] ?? EMPTY_SERIES) : EMPTY_SERIES;
}

/**
 * F-W11 3상태.
 *
 *   OFFLINE  소켓이 안 붙는다 (백엔드 자체 미응답 또는 세션 만료)
 *   INACTIVE 붙었지만 **낙상 축이 없다** — "낙상 감지 미동작"
 *   ACTIVE   낙상 판정이 실제로 흐르고 있다
 *
 * 재실만 오는 상태를 ACTIVE 로 치지 않는 것이 핵심이다. 재실은 엣지가 상시 계산하지만
 * 낙상은 클라우드 추론이 붙어야 나오고, 그 부재를 "이상 없음"으로 보이면 안 된다.
 */
export function useDetectionState(): DetectionStatus {
  const live = useLive();
  if (live.status === "unauthorized" || live.status === "reconnecting" || live.status === "idle") {
    return "OFFLINE";
  }
  if (live.status !== "open") return "OFFLINE";
  const anyFall = Object.values(live.devices).some((d) => d.fall != null);
  return anyFall ? "ACTIVE" : "INACTIVE";
}

/** 이 사용자 스코프에서 재실이 확인된 기기가 하나라도 있는가. 사이드바 요약용. */
export function useAnyPresence(): "PRESENT" | "ABSENT" | null {
  const live = useLive();
  const states = Object.values(live.devices)
    .map((d) => d.presence?.state)
    .filter((s): s is "present" | "absent" => s === "present" || s === "absent");
  if (states.length === 0) return null;
  return states.includes("present") ? "PRESENT" : "ABSENT";
}
