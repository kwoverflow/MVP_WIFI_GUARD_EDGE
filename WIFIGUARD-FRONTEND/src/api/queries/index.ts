/**
 * 조회 훅 — VITE_USE_MOCK=1 이면 목업 훅, 아니면 TanStack Query(실API).
 * 선택은 모듈 로드 시 한 번(빌드 상수)이라 훅 호출 순서가 바뀌지 않는다.
 */
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import type {
  Device,
  DetectionStatus,
  EventLogPage,
  Facility,
  FacilityMember,
  FallPage,
  FallResponse,
  LogLevel,
  Recipient,
  Resident,
  TenantConfig,
} from "@/lib/domain";
import { useStore as useMockStore } from "@/lib/mock-store";

import { useAuth } from "../auth";
import { USE_MOCK, apiFetch } from "../client";
import { qk } from "../keys";
import * as mock from "../mock/hooks";
import { useDetectionState, useLive, useMvSeries } from "../realtime/useLiveStream";
import { withLiveResidents } from "../realtime/residents";
import { ready, type QueryResultLike } from "../types";

function useAuthed(): boolean {
  return useAuth().status === "authed";
}

// ── devices ─────────────────────────────────────────────────────────
function useDevicesReal(): QueryResultLike<Device[]> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.devices(),
    queryFn: () => apiFetch<Device[]>("/devices"),
    enabled,
  });
}
export const useDevices: () => QueryResultLike<Device[]> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useDevices : useDevicesReal;

// ── residents ───────────────────────────────────────────────────────
function useResidentsReal(): QueryResultLike<Resident[]> {
  const enabled = useAuthed();
  const live = useLive();
  const query = useQuery({
    queryKey: qk.residents(),
    queryFn: () => apiFetch<Resident[]>("/residents"),
    enabled,
  });
  const data = useMemo(() => query.data ? withLiveResidents(query.data, live.status === "open" ? live.devices : {}) : undefined,
    [query.data, live.status, live.devices]);
  return { ...query, data };
}
export const useResidents: () => QueryResultLike<Resident[]> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useResidents : useResidentsReal;

// ── falls ───────────────────────────────────────────────────────────
export interface FallsParams {
  response?: FallResponse | null;
  residentId?: string | null;
  from?: string | null;
  to?: string | null;
  limit?: number;
  offset?: number;
}
function useFallsReal(params: FallsParams = {}): QueryResultLike<FallPage> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.falls(params as Record<string, unknown>),
    queryFn: () => apiFetch<FallPage>("/falls", { query: { ...params } }),
    enabled,
  });
}
function useFallsMock(params: FallsParams = {}): QueryResultLike<FallPage> {
  const r = mock.useFalls({ response: params.response, limit: params.limit });
  return ready({
    items: r.data?.items ?? [],
    total: r.data?.total ?? 0,
    limit: params.limit ?? 50,
    offset: 0,
  });
}
export const useFalls: (params?: FallsParams) => QueryResultLike<FallPage> =
  import.meta.env.VITE_USE_MOCK === "1" ? useFallsMock : useFallsReal;

// ── event logs ──────────────────────────────────────────────────────
export interface EventLogsParams {
  level?: LogLevel | null;
  q?: string | null;
  residentId?: string | null;
  from?: string | null;
  to?: string | null;
  limit?: number;
  offset?: number;
}
function useEventLogsReal(params: EventLogsParams = {}): QueryResultLike<EventLogPage> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.eventLogs(params as Record<string, unknown>),
    queryFn: () => apiFetch<EventLogPage>("/event-logs", { query: { ...params } }),
    enabled,
    refetchInterval: 15_000,
  });
}
function useEventLogsMock(params: EventLogsParams = {}): QueryResultLike<EventLogPage> {
  const r = mock.useEventLogs({ level: params.level, q: params.q, limit: params.limit });
  return ready({
    items: r.data?.items ?? [],
    total: r.data?.total ?? 0,
    limit: params.limit ?? 50,
    offset: 0,
  });
}
export const useEventLogs: (params?: EventLogsParams) => QueryResultLike<EventLogPage> =
  import.meta.env.VITE_USE_MOCK === "1" ? useEventLogsMock : useEventLogsReal;

// ── recipients ──────────────────────────────────────────────────────
function useRecipientsReal(): QueryResultLike<Recipient[]> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.recipients(),
    queryFn: () => apiFetch<Recipient[]>("/recipients"),
    enabled,
  });
}
export const useRecipients: () => QueryResultLike<Recipient[]> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useRecipients : useRecipientsReal;

// ── config ──────────────────────────────────────────────────────────
function useConfigReal(): QueryResultLike<TenantConfig> {
  const enabled = useAuthed();
  return useQuery({
    queryKey: qk.config(),
    queryFn: () => apiFetch<TenantConfig>("/config"),
    enabled,
  });
}
export const useConfig: () => QueryResultLike<TenantConfig> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useConfig : useConfigReal;

// ── facility ────────────────────────────────────────────────────────
function useFacilityReal(): QueryResultLike<Facility | null> {
  const { status, user } = useAuth();
  return useQuery({
    queryKey: qk.facility(),
    queryFn: () => apiFetch<Facility>("/facilities/me"),
    enabled: status === "authed" && user?.service === "FACILITY",
  });
}
export const useFacility: () => QueryResultLike<Facility | null> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useFacility : useFacilityReal;

function useFacilityMembersReal(): QueryResultLike<FacilityMember[]> {
  const { status, user } = useAuth();
  return useQuery({
    queryKey: qk.members(),
    queryFn: () => apiFetch<FacilityMember[]>("/facilities/me/members"),
    enabled: status === "authed" && user?.role === "ROOT",
  });
}
export const useFacilityMembers: () => QueryResultLike<FacilityMember[]> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useFacilityMembers : useFacilityMembersReal;

// ── MV 시계열 (차트) ─────────────────────────────────────────────────
/**
 * 기기 하나의 MV 시계열. 실모드는 `/ws/live` 가 쌓은 것을, 목업은 시뮬레이션을 읽는다.
 *
 * `deviceId` 가 없으면 빈 배열이고, 차트는 "감지 미동작" 플레이스홀더를 그린다.
 * 구 구현은 실모드에서 **항상** 빈 배열을 반환하는 스텁이었다.
 */
function useMvHistoryReal(deviceId?: string | null): { t: number; mv: number }[] {
  return useMvSeries(deviceId);
}
function useMvHistoryMock(_deviceId?: string | null): { t: number; mv: number }[] {
  return useMockStore((s) => s.mvHistory);
}
export const useMvHistory: (deviceId?: string | null) => { t: number; mv: number }[] =
  import.meta.env.VITE_USE_MOCK === "1" ? useMvHistoryMock : useMvHistoryReal;

// ── 감지 상태 (F-W11 3상태) ─────────────────────────────────────────
/**
 * ACTIVE  : 낙상 판정이 실제로 흐르고 있음
 * INACTIVE: 소켓은 붙었지만 **낙상 축이 없음** — "낙상 감지 미동작"
 * OFFLINE : 소켓이 붙지 않음 (백엔드 미응답 또는 세션 만료)
 *
 * 재실만 오는 상태를 ACTIVE 로 치지 않는다. 재실은 엣지가 상시 계산하지만 낙상은 클라우드
 * 추론이 붙어야 나오고, 그 부재를 "이상 없음"으로 보이면 안 된다 (안전 요구 F-W11).
 * 구 구현은 `/health` ping 만 보고 **ACTIVE 를 반환하는 경로가 아예 없었다.**
 */
const useDetectionStatusReal = useDetectionState;
function useDetectionStatusMock(): DetectionStatus {
  const running = useMockStore((s) => s.running);
  const backendConnected = useMockStore((s) => s.backendConnected);
  const { user } = useAuth();
  const active = user?.service === "FACILITY" ? running : backendConnected;
  return active ? "ACTIVE" : "INACTIVE";
}
export const useDetectionStatus: () => DetectionStatus =
  import.meta.env.VITE_USE_MOCK === "1" ? useDetectionStatusMock : useDetectionStatusReal;
