/**
 * 목업 스토어(mock-store.ts, snake 필드 · epoch ms) ↔ 도메인 타입(camel · ISO) 변환.
 * VITE_USE_MOCK=1 일 때만 번들에 남는다.
 */
import type {
  Device,
  EventLogEntry,
  Facility,
  FacilityMember,
  FallEvent,
  Recipient,
  RecipientRole,
  Resident,
  TenantConfig,
} from "@/lib/domain";
import type * as M from "@/lib/mock-store";

export const EPOCH_ISO = new Date(0).toISOString();
export const iso = (ms: number | null | undefined) => (ms ? new Date(ms).toISOString() : null);

const ROLE_TO_CODE: Record<M.Recipient["role"], RecipientRole> = {
  가족: "FAMILY",
  요양사: "CAREGIVER",
  관리자: "ADMIN",
};
const CODE_TO_ROLE: Record<RecipientRole, M.Recipient["role"]> = {
  FAMILY: "가족",
  CAREGIVER: "요양사",
  ADMIN: "관리자",
};

export function toDevice(d: M.Device): Device {
  return {
    id: d.id,
    name: d.name,
    room: d.room,
    connection: d.connection,
    mqttTopic: d.connection === "MQTT" ? d.mqttTopic : null,
    serialPort: d.serialPort ?? null,
    mac: d.mac,
    fw: d.fw,
    online: d.online ?? false,
    lastSeenAt: iso(d.lastSeen),
    baseRssi: d.base_rssi,
    currentRssi: d.current_rssi,
    agc: d.agc,
    noiseFloor: d.noise_floor,
    calibrating: d.calibrating,
    calibrationStage: d.calibrationStage,
    calibrationProgress: d.calibrationProgress,
    presenceMvThreshold: d.presence_mv_threshold,
    wanderBaseline: d.wander_baseline,
    facilityId: d.facilityId ?? null,
    ownerUserId: d.ownerUserId ?? null,
    createdAt: EPOCH_ISO,
    updatedAt: EPOCH_ISO,
  };
}

export function fromDevice(d: Device): M.Device {
  return {
    id: d.id,
    name: d.name,
    room: d.room,
    mqttTopic: d.mqttTopic ?? (d.serialPort ? `serial://${d.serialPort}` : ""),
    mac: d.mac ?? "",
    fw: d.fw ?? "v1.4.2",
    online: d.online ?? false,
    lastSeen: d.lastSeenAt ? new Date(d.lastSeenAt).getTime() : Date.now(),
    base_rssi: d.baseRssi ?? -60,
    current_rssi: d.currentRssi ?? -60,
    agc: d.agc ?? 25,
    noise_floor: d.noiseFloor ?? -92,
    calibrating: d.calibrating,
    calibrationStage: d.calibrationStage,
    calibrationProgress: d.calibrationProgress,
    presence_mv_threshold: d.presenceMvThreshold ?? 1.8,
    wander_baseline: d.wanderBaseline ?? 0.5,
    connection: d.connection,
    serialPort: d.serialPort ?? undefined,
    facilityId: d.facilityId ?? undefined,
    ownerUserId: d.ownerUserId ?? undefined,
  };
}

export function toResident(r: M.Resident): Resident {
  return {
    id: r.id,
    name: r.name,
    room: r.room,
    age: r.age,
    caregiver: r.caregiver,
    deviceId: r.deviceId || null,
    deviceIds: r.deviceIds && r.deviceIds.length ? r.deviceIds : r.deviceId ? [r.deviceId] : [],
    thresholdOverride: r.thresholdOverride ?? null,
    facilityId: r.facilityId ?? null,
    ownerUserId: r.ownerUserId ?? null,
    createdAt: EPOCH_ISO,
    updatedAt: EPOCH_ISO,
    state: r.state,
    mv: r.mv,
    wander: r.wander,
    presence: r.presence,
    lastActivityAt: iso(r.lastActivityAt),
    confidence: r.confidence,
    online: r.online,
  };
}

export function fromResident(r: Resident, prev?: M.Resident): M.Resident {
  return {
    id: r.id,
    name: r.name,
    room: r.room,
    age: r.age ?? 65,
    caregiver: r.caregiver ?? "",
    deviceId: r.deviceId ?? "",
    deviceIds: r.deviceIds,
    facilityId: r.facilityId ?? undefined,
    ownerUserId: r.ownerUserId ?? undefined,
    thresholdOverride: r.thresholdOverride ?? undefined,
    state: prev?.state ?? "IDLE",
    mv: prev?.mv ?? 0,
    wander: prev?.wander ?? 0,
    presence: prev?.presence ?? "ABSENT",
    lastActivityAt: prev?.lastActivityAt ?? 0,
    confidence: prev?.confidence ?? 0,
    online: r.online ?? prev?.online ?? true,
  };
}

export function toFall(
  f: M.FallEvent,
  scope: { facilityId: string | null; ownerUserId: string | null },
): FallEvent {
  return {
    id: f.id,
    residentId: f.residentId,
    residentName: f.residentName,
    room: f.room,
    deviceId: null,
    occurredAt: new Date(f.timestamp).toISOString(),
    confidence: f.confidence,
    durationS: f.duration,
    source: f.id.startsWith("f-seed") ? "SEED" : "SIMULATED",
    response: f.response,
    respondedBy: null,
    respondedAt: null,
    facilityId: scope.facilityId,
    ownerUserId: scope.ownerUserId,
    createdAt: new Date(f.timestamp).toISOString(),
  };
}

export function toLog(l: M.EventLogEntry, index: number): EventLogEntry {
  return {
    id: l.ts * 10 + index, // 목업 로그에는 id 가 없다 — 표시용 유일키
    ts: new Date(l.ts).toISOString(),
    level: l.level,
    msg: l.msg,
    residentId: l.residentId ?? null,
    deviceId: null,
    fallEventId: null,
    actorUserId: null,
  };
}

export function toRecipient(
  r: M.Recipient,
  scope: { facilityId: string | null; ownerUserId: string | null },
): Recipient {
  return {
    id: r.id,
    name: r.name,
    role: ROLE_TO_CODE[r.role],
    phone: r.phone,
    email: r.email,
    emailEnabled: r.emailEnabled,
    sms: r.sms,
    push: r.push,
    ars: r.ars,
    ntfyServer: null,
    ntfyTopic: null,
    enabled: true,
    residentId: r.residentId ?? null,
    facilityId: scope.facilityId,
    ownerUserId: scope.ownerUserId,
    createdAt: EPOCH_ISO,
    updatedAt: EPOCH_ISO,
  };
}

export function fromRecipient(r: Recipient): M.Recipient {
  return {
    id: r.id,
    name: r.name,
    role: CODE_TO_ROLE[r.role],
    phone: r.phone ?? "",
    email: r.email ?? "",
    emailEnabled: r.emailEnabled,
    sms: r.sms,
    push: r.push,
    ars: r.ars,
    residentId: r.residentId ?? undefined,
  };
}

/** 목업 PipelineConfig(16필드) 중 UI 가 쓰는 5개만. wander_threshold(절대값)는 배율 축과 다르지만 목업 전용이라 그대로 싣는다. */
export function toConfig(c: M.PipelineConfig): TenantConfig {
  return {
    presenceMvThreshold: c.mv_threshold,
    wanderRatioThreshold: Math.max(1, c.wander_threshold),
    presenceTimeoutS: c.presence_timeout_s,
    threshold: c.threshold,
    cooldownSeconds: c.cooldown_s,
    updatedAt: EPOCH_ISO,
    updatedBy: null,
  };
}

export function toFacility(f: M.Facility): Facility {
  return {
    id: f.id,
    name: f.name,
    inviteCode: f.code,
    rootUserId: f.rootUserId,
    createdAt: EPOCH_ISO,
  };
}

export function toMember(u: M.UserAccount): FacilityMember {
  return {
    id: u.id,
    email: u.email,
    name: u.name,
    role: u.role,
    onboarded: u.onboarded,
    createdAt: EPOCH_ISO,
  };
}
