import { describe, expect, it } from "vitest";
import type { DeviceLive } from "@/api/realtime/types";
import type { Resident } from "@/lib/domain";
import { monitoringSummary, resourceValue } from "./monitoring-summary";

const connected = (online: boolean | null = true) =>
  ({
    device_id: "sensor-1",
    online,
    last_seen_at: "2026-10-13T03:00:00Z",
    link: null,
    presence: { state: "present" },
    fall: { detect_state: "IDLE" },
  }) as DeviceLive;

describe("user-facing safety summaries", () => {
  it("does not label missing evidence as absent or safe", () => {
    const value = monitoringSummary(null, undefined, "INACTIVE");
    expect(value.presence).toBe("판단 불가");
    expect(value.fall).toBe("감지 중지");
    expect(value.connected).toBe(false);
  });
  it("discards the old resident state when the socket is disconnected", () => {
    const oldResident = { presence: "PRESENT", state: "IDLE", online: true } as Resident;
    expect(monitoringSummary(connected(), oldResident, "OFFLINE").presence).toBe("판단 불가");
    expect(monitoringSummary(connected(), oldResident, "OFFLINE").fall).toBe("감지 중지");
  });
  it("does not reuse stale blocks after sensor disconnection", () => {
    expect(monitoringSummary(connected(false), undefined, "ACTIVE").presence).toBe("판단 불가");
    expect(monitoringSummary(connected(false), undefined, "ACTIVE").fall).toBe("감지 중지");
  });
  it("does not infer fall status from presence alone", () => {
    const live = connected();
    live.fall = null;
    const value = monitoringSummary(live, undefined, "INACTIVE");
    expect(value.presence).toBe("재실");
    expect(value.fall).toBe("감지 대기");
  });
  it("marks a received fall clearly without changing its backend state", () => {
    const live = connected();
    live.fall!.detect_state = "FALL";
    expect(monitoringSummary(live, undefined, "ACTIVE").fallTone).toBe("danger");
  });
  it("supports the existing explicit mock build", () => {
    const resident = { presence: "PRESENT", state: "SUSPECT", online: true } as Resident;
    expect(monitoringSummary(null, resident, "ACTIVE", true).fall).toBe("낙상 의심");
  });
  it("shows only fresh complete measured resources, including a genuine zero", () => {
    expect(resourceValue(42, false, "%")).toBe("미측정");
    expect(resourceValue(null, true, "%")).toBe("미측정");
    expect(resourceValue(Number.NaN, true, "%")).toBe("미측정");
    expect(resourceValue(0, true, "%")).toBe("0.0%");
    expect(resourceValue(512, true, " MiB")).toBe("512.0 MiB");
  });
});
