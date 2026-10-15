import type { Resident } from "@/lib/domain";
import type { DeviceLive } from "./types";

/** Identity comes from REST; current detection comes only from the live stream. */
export function withLiveResidents(residents: Resident[], devices: Record<string, DeviceLive>): Resident[] {
  return residents.map((resident) => {
    const live = devices[resident.deviceId ?? resident.deviceIds[0] ?? ""];
    const current = live?.online === true;
    return {
      ...resident,
      online: live?.online ?? null,
      presence: current && live?.presence ? (live.presence.state === "present" ? "PRESENT" : "ABSENT") : null,
      state: current ? live?.fall?.detect_state ?? null : null,
      mv: current ? live?.presence?.mv_current ?? null : null,
      wander: current ? live?.presence?.wander_current ?? null : null,
      confidence: current ? live?.fall?.proba_fall ?? null : null,
      lastActivityAt: current && live?.presence?.last_activity_at != null
        ? new Date(live.presence.last_activity_at * 1000).toISOString() : null,
    };
  });
}
