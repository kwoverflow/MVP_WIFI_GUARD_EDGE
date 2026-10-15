/**
 * VITE_USE_MOCK=1 전용 훅 — mock-store 를 읽고 쓰되 도메인 타입으로 변환해 돌려준다.
 * 스코핑(FACILITY facilityId / HOME ownerUserId)은 기존 라우트가 하던 클라이언트 필터를 그대로 옮겼다.
 */
import { useMemo } from "react";

import type {
  Device,
  DeviceInput,
  DevicePatch,
  EventLogEntry,
  EventLogInput,
  Facility,
  FacilityMember,
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
  UserAccount,
} from "@/lib/domain";
import * as mock from "@/lib/mock-store";

import { ready, syncMutation, type MutationLike, type QueryResultLike } from "../types";
import * as A from "./adapters";

function scopeOf(): { facilityId: string | null; ownerUserId: string | null } {
  const u = mock.currentUser();
  if (!u) return { facilityId: null, ownerUserId: null };
  return u.service === "FACILITY"
    ? { facilityId: u.facilityId ?? null, ownerUserId: null }
    : { facilityId: null, ownerUserId: u.id };
}

function inScope(
  x: { facilityId?: string; ownerUserId?: string },
  u: mock.UserAccount | null,
): boolean {
  if (!u) return false;
  return u.service === "FACILITY" ? x.facilityId === u.facilityId : x.ownerUserId === u.id;
}

// ── 조회 ────────────────────────────────────────────────────────────
export function useDevices(): QueryResultLike<Device[]> {
  const devices = mock.useStore((s) => s.devices);
  const u = mock.useCurrentUser();
  return ready(useMemo(() => devices.filter((d) => inScope(d, u)).map(A.toDevice), [devices, u]));
}

export function useResidents(): QueryResultLike<Resident[]> {
  const residents = mock.useStore((s) => s.residents);
  const u = mock.useCurrentUser();
  return ready(
    useMemo(() => residents.filter((r) => inScope(r, u)).map(A.toResident), [residents, u]),
  );
}

export function useFalls(params: {
  response?: FallResponse | null;
  limit?: number;
}): QueryResultLike<{ items: FallEvent[]; total: number }> {
  const falls = mock.useStore((s) => s.falls);
  const residents = mock.useStore((s) => s.residents);
  const u = mock.useCurrentUser();
  return ready(
    useMemo(() => {
      const scope = scopeOf();
      const ids = new Set(residents.filter((r) => inScope(r, u)).map((r) => r.id));
      let items = falls.filter(
        (f) => ids.has(f.residentId) || (u?.service === "HOME" && f.residentId === u.id),
      );
      if (params.response) items = items.filter((f) => f.response === params.response);
      const mapped = items.map((f) => A.toFall(f, scope));
      return { items: params.limit ? mapped.slice(0, params.limit) : mapped, total: mapped.length };
    }, [falls, residents, u, params.response, params.limit]),
  );
}

export function useEventLogs(params: {
  level?: string | null;
  q?: string | null;
  limit?: number;
}): QueryResultLike<{ items: EventLogEntry[]; total: number }> {
  const logs = mock.useScopedLogs();
  return ready(
    useMemo(() => {
      let items = logs;
      if (params.level) items = items.filter((l) => l.level === params.level);
      if (params.q) {
        const q = params.q.toLowerCase();
        items = items.filter((l) => l.msg.toLowerCase().includes(q));
      }
      const mapped = items.map(A.toLog);
      return { items: params.limit ? mapped.slice(0, params.limit) : mapped, total: mapped.length };
    }, [logs, params.level, params.q, params.limit]),
  );
}

export function useRecipients(): QueryResultLike<Recipient[]> {
  const recipients = mock.useStore((s) => s.recipients);
  return ready(useMemo(() => recipients.map((r) => A.toRecipient(r, scopeOf())), [recipients]));
}

export function useConfig(): QueryResultLike<TenantConfig> {
  const config = mock.useStore((s) => s.config);
  return ready(useMemo(() => A.toConfig(config), [config]));
}

export function useFacility(): QueryResultLike<Facility | null> {
  const f = mock.useCurrentFacility();
  return ready(f ? A.toFacility(f) : null);
}

export function useFacilityMembers(): QueryResultLike<FacilityMember[]> {
  const users = mock.useStore((s) => s.users);
  const f = mock.useCurrentFacility();
  return ready(
    useMemo(
      () => (f ? users.filter((x) => x.facilityId === f.id).map(A.toMember) : []),
      [users, f],
    ),
  );
}

// ── 변경 ────────────────────────────────────────────────────────────
export function useCreateDevice(): MutationLike<DeviceInput, Device> {
  return syncMutation((input) => {
    const scope = scopeOf();
    const id = `d-${Date.now()}`;
    const dev: Device = {
      id,
      name: input.name,
      room: input.room,
      connection: input.connection,
      mqttTopic:
        input.connection === "MQTT"
          ? (input.mqttTopic ??
            `wifiguard/${scope.facilityId ?? `home-${scope.ownerUserId}`}/${id}`)
          : null,
      serialPort: input.serialPort ?? null,
      mac:
        input.mac ??
        `AA:BB:CC:${Array.from({ length: 3 }, () =>
          Math.floor(Math.random() * 256)
            .toString(16)
            .padStart(2, "0")
            .toUpperCase(),
        ).join(":")}`,
      fw: input.fw ?? "v1.4.2",
      online: true,
      lastSeenAt: new Date().toISOString(),
      baseRssi: -60,
      currentRssi: -60,
      agc: 25,
      noiseFloor: -92,
      calibrating: false,
      calibrationStage: "IDLE",
      calibrationProgress: 0,
      presenceMvThreshold: 1.8,
      wanderBaseline: 0.5,
      facilityId: scope.facilityId,
      ownerUserId: scope.ownerUserId,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    mock.upsertDevice(A.fromDevice(dev));
    return dev;
  });
}

export function useUpdateDevice(): MutationLike<{ id: string; patch: DevicePatch }, Device> {
  return syncMutation(({ id, patch }) => {
    const prev = mock.getState().devices.find((d) => d.id === id);
    if (!prev) throw new Error("장치를 찾을 수 없습니다");
    const merged: Device = { ...A.toDevice(prev) };
    for (const [k, v] of Object.entries(patch))
      if (v !== undefined) (merged as Record<string, unknown>)[k] = v;
    mock.upsertDevice(A.fromDevice(merged));
    return merged;
  });
}

export function useDeleteDevice(): MutationLike<string, void> {
  return syncMutation((id) => {
    mock.deleteDevice(id);
  });
}

function normalizeMapping(
  deviceId: string | null | undefined,
  deviceIds: string[] | null | undefined,
) {
  const ids = deviceIds ?? (deviceId ? [deviceId] : []);
  const primary = deviceId && ids.includes(deviceId) ? deviceId : (ids[0] ?? null);
  return { primary, ids };
}

export function useCreateResident(): MutationLike<ResidentInput, Resident> {
  return syncMutation((input) => {
    const scope = scopeOf();
    const { primary, ids } = normalizeMapping(input.deviceId, input.deviceIds);
    const r: Resident = {
      id: `r-${Date.now()}`,
      name: input.name,
      room: input.room,
      age: input.age ?? null,
      caregiver: input.caregiver ?? null,
      deviceId: primary,
      deviceIds: ids,
      thresholdOverride: input.thresholdOverride ?? null,
      facilityId: scope.facilityId,
      ownerUserId: scope.ownerUserId,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      state: "IDLE",
      mv: 0,
      wander: 0,
      presence: "ABSENT",
      lastActivityAt: null,
      confidence: 0,
      online: true,
    };
    mock.upsertResident(A.fromResident(r));
    return r;
  });
}

export function useUpdateResident(): MutationLike<{ id: string; patch: ResidentPatch }, Resident> {
  return syncMutation(({ id, patch }) => {
    const prev = mock.getState().residents.find((r) => r.id === id);
    if (!prev) throw new Error("거주자를 찾을 수 없습니다");
    const cur = A.toResident(prev);
    const { primary, ids } = normalizeMapping(
      patch.deviceId !== undefined ? patch.deviceId : cur.deviceId,
      patch.deviceIds !== undefined ? patch.deviceIds : cur.deviceIds,
    );
    const merged: Resident = {
      ...cur,
      name: patch.name ?? cur.name,
      room: patch.room ?? cur.room,
      age: patch.age ?? cur.age,
      caregiver: patch.caregiver ?? cur.caregiver,
      thresholdOverride: patch.clearThresholdOverride
        ? null
        : (patch.thresholdOverride ?? cur.thresholdOverride),
      deviceId: primary,
      deviceIds: ids,
    };
    mock.upsertResident(A.fromResident(merged, prev));
    return merged;
  });
}

export function useDeleteResident(): MutationLike<string, void> {
  return syncMutation((id) => {
    mock.deleteResident(id);
  });
}

export function useUpdateFallResponse(): MutationLike<
  { id: string; response: FallResponse },
  void
> {
  return syncMutation(({ id, response }) => {
    if (mock.getState().alarm?.id === id) mock.acknowledgeAlarm(response);
    else mock.updateResponse(id, response);
  });
}

export function useSimulateFall(): MutationLike<{ residentId?: string | null }, FallEvent | null> {
  return syncMutation(({ residentId }) => {
    mock.simulateFall(residentId ?? undefined);
    const latest = mock.getState().falls[0];
    return latest ? A.toFall(latest, scopeOf()) : null;
  });
}

export function usePostEventLog(): MutationLike<EventLogInput, void> {
  return syncMutation((input) => {
    mock.addLog(input.level, input.msg, input.residentId ?? undefined);
  });
}

export function useCreateRecipient(): MutationLike<RecipientInput, Recipient> {
  return syncMutation((input) => {
    const rec: Recipient = {
      id: `n-${Date.now()}`,
      name: input.name,
      role: input.role,
      phone: input.phone ?? null,
      email: input.email ?? null,
      emailEnabled: input.emailEnabled ?? false,
      sms: input.sms ?? false,
      push: input.push ?? false,
      ars: input.ars ?? false,
      ntfyServer: input.ntfyServer ?? null,
      ntfyTopic: input.ntfyTopic ?? null,
      enabled: input.enabled ?? true,
      residentId: input.residentId ?? null,
      ...scopeOf(),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    mock.upsertRecipient(A.fromRecipient(rec));
    return rec;
  });
}

export function useUpdateRecipient(): MutationLike<
  { id: string; patch: RecipientPatch },
  Recipient
> {
  return syncMutation(({ id, patch }) => {
    const prev = mock.getState().recipients.find((r) => r.id === id);
    if (!prev) throw new Error("수신자를 찾을 수 없습니다");
    const cur = A.toRecipient(prev, scopeOf());
    const merged: Recipient = { ...cur };
    for (const [k, v] of Object.entries(patch))
      if (v !== undefined && v !== null && k !== "clearResident")
        (merged as Record<string, unknown>)[k] = v;
    if (patch.clearResident) merged.residentId = null;
    mock.upsertRecipient(A.fromRecipient(merged));
    return merged;
  });
}

export function useDeleteRecipient(): MutationLike<string, void> {
  return syncMutation((id) => {
    mock.deleteRecipient(id);
  });
}

export function useUpdateConfig(): MutationLike<TenantConfigInput, TenantConfig> {
  return syncMutation((input) => {
    mock.updateConfig({
      mv_threshold: input.presenceMvThreshold,
      wander_threshold: input.wanderRatioThreshold,
      presence_timeout_s: input.presenceTimeoutS,
      threshold: input.threshold,
      cooldown_s: input.cooldownSeconds,
    });
    return A.toConfig(mock.getState().config);
  });
}

export function useRegenerateInviteCode(): MutationLike<void, Facility | null> {
  return syncMutation(() => {
    const f = mock.currentUser()?.facilityId;
    if (f) mock.regenerateInviteCode(f);
    const fac = mock.getState().facilities.find((x) => x.id === f);
    return fac ? A.toFacility(fac) : null;
  });
}

export function useRemoveMember(): MutationLike<string, void> {
  return syncMutation((userId) => {
    mock.removeMember(userId);
  });
}

export function usePatchAccount(): MutationLike<
  { name?: string | null; email?: string | null },
  UserAccount | null
> {
  return syncMutation((patch) => {
    mock.updateAccount({ name: patch.name ?? undefined, email: patch.email ?? undefined });
    const u = mock.currentUser();
    return u
      ? {
          id: u.id,
          email: u.email,
          name: u.name,
          service: u.service,
          role: u.role,
          facilityId: u.facilityId ?? null,
          onboarded: u.onboarded,
          createdAt: A.EPOCH_ISO,
        }
      : null;
  });
}

export function useChangePassword(): MutationLike<
  { currentPassword: string; newPassword: string },
  void
> {
  return syncMutation(({ currentPassword, newPassword }) => {
    const u = mock.currentUser();
    if (!u || u.password !== currentPassword) throw new Error("현재 비밀번호가 올바르지 않습니다.");
    mock.updateAccount({ password: newPassword });
  });
}
