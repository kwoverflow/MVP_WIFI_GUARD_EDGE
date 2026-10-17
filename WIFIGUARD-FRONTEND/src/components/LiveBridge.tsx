/**
 * 앱 전역 실시간 브리지 — 소켓을 열어 두고, 낙상 전이를 알람으로 올린다.
 *
 * 구 `BackendDetectionBridge` 를 대체한다. 그쪽에는 두 가지 결함이 있었다.
 *
 * 1. **알람이 뜨지 않았다.** WS 샘플을 `mock-store.applyBackendDetection()` 으로 흘렸는데,
 *    `VITE_USE_MOCK=0`(기본)에서는 `useAlarm` 이 `alarmStore` 를 읽고 `useResidents` 는
 *    실 API 를 읽는다. 즉 브리지가 쓴 값을 **아무도 읽지 않았고**, FALL 이 와도 모달이
 *    뜨지 않았다. 이제 실 스토어(`alarmStore`)에 직접 올린다.
 * 2. **HOME 전용이었다.** `user?.service !== "HOME"` 이면 `null` 을 반환해 FACILITY 는
 *    실시간이 아예 없었다. 다기기 관제가 정작 필요한 쪽인데 그랬다.
 *
 * 이 컴포넌트는 `AuthGate` 안에 상주하므로 어느 페이지에 있어도 낙상 알람이 뜬다.
 * 화면에 값을 그리는 것은 각 컴포넌트가 `useLiveDevice()` 로 직접 한다 — 브리지가
 * 중간 스토어를 거치면 그게 다시 1번 결함의 씨앗이 된다.
 */

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useLive } from "@/api/realtime/useLiveStream";
import { apiFetch } from "@/api/client";
import { authStore } from "@/api/auth";
import { reconcileAlarm } from "@/lib/persisted-alarm";
import { qk } from "@/api/keys";
import { useResidents } from "@/api/queries";
import { alarmStore } from "@/lib/alarm-store";
import type { FallEvent } from "@/lib/domain";

export function LiveBridge() {
  const live = useLive();
  const { data: residents } = useResidents();
  const queryClient = useQueryClient();
  /** 기기별 직전 낙상 상태 — IDLE→FALL 전이에서만 알람을 올린다. */
  const prevState = useRef<Record<string, string | undefined>>({});

  useEffect(() => {
    for (const device of Object.values(live.devices)) {
      const fall = device.fall;
      const before = prevState.current[device.device_id];
      const now = fall?.detect_state;
      prevState.current[device.device_id] = now;

      if (now !== "FALL" || before === "FALL") continue;

      // 이 기기를 주 장치로 쓰는 거주자를 찾는다. 없으면 기기 이름 대신 빈칸으로 둔다.
      const resident = residents?.find((r) => r.deviceId === device.device_id);
      const occurredAt = fall?.updated_at ?? new Date().toISOString();
      const event: FallEvent = {
        id: fall?.fall_event_id ?? `live-${device.device_id}-${occurredAt}`,
        residentId: resident?.id ?? null,
        residentName: resident?.name ?? "미지정 거주자",
        room: resident?.room ?? "",
        deviceId: device.device_id,
        occurredAt,
        confidence: fall?.proba_fall ?? 0,
        durationS: 0,
        source: "EDGE",
        response: "PENDING",
        respondedAt: null,
        respondedBy: null,
        facilityId: null,
        ownerUserId: null,
        createdAt: occurredAt,
      };
      if (fall?.fall_event_id) {
        // A reconnect snapshot can contain a historical FALL that another
        // operator has already handled. The persisted response is authoritative.
        const account = authStore.get().user?.id;
        void reconcileAlarm(event, () => apiFetch<FallEvent>(`/falls/${fall.fall_event_id}`),
          alarmStore.raise, () => account != null && authStore.get().user?.id === account);
      } else {
        alarmStore.raise(event);
      }

      // 서버가 fall_events 행을 만들었을 것이다(M5). 이력·로그를 다시 읽어
      // 화면의 임시 이벤트가 실제 행으로 대체되게 한다.
      void queryClient.invalidateQueries({ queryKey: qk.falls() });
      void queryClient.invalidateQueries({ queryKey: qk.eventLogs() });
    }
  }, [live.devices, residents, queryClient]);

  return null;
}
