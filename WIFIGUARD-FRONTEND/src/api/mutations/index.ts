/**
 * 변경 훅 — 실API 는 useMutation + 무효화, VITE_USE_MOCK=1 이면 목업 스토어 변이.
 * 서버가 이벤트 로그를 남기므로 모든 변경 후 event-logs 를 함께 무효화한다.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import type {
  Device,
  DeviceInput,
  DevicePatch,
  EventLogInput,
  Facility,
  FallEvent,
  FallResponse,
  Recipient,
  RecipientInput,
  RecipientPatch,
  Resident,
  ResidentInput,
  ResidentPatch,
  TenantConfig,
  TenantConfigInput,
  TokenPair,
  UserAccount,
} from "@/lib/domain";

import { authStore } from "../auth";
import { USE_MOCK, apiFetch, storeTokenPair } from "../client";
import { qk } from "../keys";
import * as mock from "../mock/hooks";
import type { MutationLike } from "../types";

function useInvalidate() {
  const qc = useQueryClient();
  return (...roots: string[]) =>
    Promise.all(roots.map((r) => qc.invalidateQueries({ queryKey: [r] })));
}

// ── devices ─────────────────────────────────────────────────────────
function useCreateDeviceReal(): MutationLike<DeviceInput, Device> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (input: DeviceInput) =>
      apiFetch<Device>("/devices", { method: "POST", body: input }),
    onSuccess: () => inv("devices", "event-logs"),
  });
}
export const useCreateDevice =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useCreateDevice : useCreateDeviceReal;

function useUpdateDeviceReal(): MutationLike<{ id: string; patch: DevicePatch }, Device> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: DevicePatch }) =>
      apiFetch<Device>(`/devices/${id}`, { method: "PATCH", body: patch }),
    onSuccess: () => inv("devices", "event-logs"),
  });
}
export const useUpdateDevice =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useUpdateDevice : useUpdateDeviceReal;

function useDeleteDeviceReal(): MutationLike<string, void> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/devices/${id}`, { method: "DELETE" }),
    onSuccess: () => inv("devices", "residents", "event-logs"),
  });
}
export const useDeleteDevice =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useDeleteDevice : useDeleteDeviceReal;

// ── residents ───────────────────────────────────────────────────────
function useCreateResidentReal(): MutationLike<ResidentInput, Resident> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (input: ResidentInput) =>
      apiFetch<Resident>("/residents", { method: "POST", body: input }),
    onSuccess: () => inv("residents", "event-logs"),
  });
}
export const useCreateResident =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useCreateResident : useCreateResidentReal;

function useUpdateResidentReal(): MutationLike<{ id: string; patch: ResidentPatch }, Resident> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ResidentPatch }) =>
      apiFetch<Resident>(`/residents/${id}`, { method: "PATCH", body: patch }),
    onSuccess: () => inv("residents", "event-logs"),
  });
}
export const useUpdateResident =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useUpdateResident : useUpdateResidentReal;

function useDeleteResidentReal(): MutationLike<string, void> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/residents/${id}`, { method: "DELETE" }),
    onSuccess: () => inv("residents", "recipients", "falls", "event-logs"),
  });
}
export const useDeleteResident =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useDeleteResident : useDeleteResidentReal;

// ── falls ───────────────────────────────────────────────────────────
function useUpdateFallResponseReal(): MutationLike<
  { id: string; response: FallResponse },
  FallEvent
> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, response }: { id: string; response: FallResponse }) =>
      apiFetch<FallEvent>(`/falls/${id}/response`, { method: "PATCH", body: { response } }),
    onSuccess: () => inv("falls", "event-logs"),
  });
}
export const useUpdateFallResponse: () => MutationLike<
  { id: string; response: FallResponse },
  unknown
> = import.meta.env.VITE_USE_MOCK === "1" ? mock.useUpdateFallResponse : useUpdateFallResponseReal;

function useSimulateFallReal(): MutationLike<{ residentId?: string | null }, FallEvent | null> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (vars: { residentId?: string | null }) =>
      apiFetch<FallEvent>("/falls/simulate", {
        method: "POST",
        body: { residentId: vars.residentId ?? null },
      }),
    onSuccess: () => inv("falls", "event-logs"),
  });
}
export const useSimulateFall =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useSimulateFall : useSimulateFallReal;

// ── event logs ──────────────────────────────────────────────────────
function usePostEventLogReal(): MutationLike<EventLogInput, unknown> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (input: EventLogInput) =>
      apiFetch<unknown>("/event-logs", { method: "POST", body: input }),
    onSuccess: () => inv("event-logs"),
  });
}
export const usePostEventLog: () => MutationLike<EventLogInput, unknown> =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.usePostEventLog : usePostEventLogReal;

// ── recipients ──────────────────────────────────────────────────────
function useCreateRecipientReal(): MutationLike<RecipientInput, Recipient> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (input: RecipientInput) =>
      apiFetch<Recipient>("/recipients", { method: "POST", body: input }),
    onSuccess: () => inv("recipients", "event-logs"),
  });
}
export const useCreateRecipient =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useCreateRecipient : useCreateRecipientReal;

function useUpdateRecipientReal(): MutationLike<{ id: string; patch: RecipientPatch }, Recipient> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: RecipientPatch }) =>
      apiFetch<Recipient>(`/recipients/${id}`, { method: "PATCH", body: patch }),
    onSuccess: () => inv("recipients"),
  });
}
export const useUpdateRecipient =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useUpdateRecipient : useUpdateRecipientReal;

function useDeleteRecipientReal(): MutationLike<string, void> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/recipients/${id}`, { method: "DELETE" }),
    onSuccess: () => inv("recipients", "event-logs"),
  });
}
export const useDeleteRecipient =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useDeleteRecipient : useDeleteRecipientReal;

// ── config ──────────────────────────────────────────────────────────
function useUpdateConfigReal(): MutationLike<TenantConfigInput, TenantConfig> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (input: TenantConfigInput) =>
      apiFetch<TenantConfig>("/config", { method: "PUT", body: input }),
    onSuccess: () => inv("config", "event-logs"),
  });
}
export const useUpdateConfig =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useUpdateConfig : useUpdateConfigReal;

// ── facility ────────────────────────────────────────────────────────
function useRegenerateInviteCodeReal(): MutationLike<void, Facility | null> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: () => apiFetch<Facility>("/facilities/me/invite-code", { method: "POST" }),
    onSuccess: async () => {
      await inv("facility", "event-logs");
      await authStore.refreshMe();
    },
  });
}
export const useRegenerateInviteCode: () => MutationLike<void, Facility | null> =
  import.meta.env.VITE_USE_MOCK === "1"
    ? mock.useRegenerateInviteCode
    : useRegenerateInviteCodeReal;

function useRemoveMemberReal(): MutationLike<string, void> {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (userId: string) =>
      apiFetch<void>(`/facilities/me/members/${userId}`, { method: "DELETE" }),
    onSuccess: () => inv("facility", "event-logs"),
  });
}
export const useRemoveMember =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useRemoveMember : useRemoveMemberReal;

// ── account ─────────────────────────────────────────────────────────
function usePatchAccountReal(): MutationLike<
  { name?: string | null; email?: string | null },
  UserAccount | null
> {
  return useMutation({
    mutationFn: (patch: { name?: string | null; email?: string | null }) =>
      apiFetch<UserAccount>("/account", { method: "PATCH", body: patch }),
    onSuccess: () => authStore.refreshMe(),
  });
}
export const usePatchAccount =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.usePatchAccount : usePatchAccountReal;

function useChangePasswordReal(): MutationLike<
  { currentPassword: string; newPassword: string },
  void
> {
  return useMutation({
    mutationFn: async (vars: { currentPassword: string; newPassword: string }) => {
      const pair = await apiFetch<TokenPair>("/account/password", { method: "POST", body: vars });
      storeTokenPair(pair);
    },
    onSuccess: () => authStore.refreshMe(),
  });
}
export const useChangePassword =
  import.meta.env.VITE_USE_MOCK === "1" ? mock.useChangePassword : useChangePasswordReal;
