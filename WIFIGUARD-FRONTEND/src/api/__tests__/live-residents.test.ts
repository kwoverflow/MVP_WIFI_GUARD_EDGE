import { describe, expect, it } from "vitest";
import { withLiveResidents } from "../realtime/residents";
import type { Resident } from "@/lib/domain";
import type { DeviceLive } from "../realtime/types";

const resident = { id: "r1", name: "시험", deviceId: "d1", deviceIds: ["d1"], presence: "ABSENT", state: "FALL" } as Resident;
const live = { device_id: "d1", online: true, presence: { state: "present", mv_current: 5, wander_current: 2, last_activity_at: 100 },
  fall: { detect_state: "IDLE", proba_fall: .1 } } as DeviceLive;

describe("REST 신원과 실시간 판정 결합", () => {
  it("헤더와 목록도 현재 WS 재실·낙상 값을 표시한다", () => {
    const value = withLiveResidents([resident], { d1: live })[0];
    expect(value).toMatchObject({ id: "r1", presence: "PRESENT", state: "IDLE", mv: 5, confidence: .1 });
  });
  it("연결이 없으면 이전 REST 판정을 정상 상태로 재사용하지 않는다", () => {
    expect(withLiveResidents([resident], {})[0]).toMatchObject({ presence: null, state: null, online: null });
    expect(withLiveResidents([resident], { d1: { ...live, online: false } })[0]).toMatchObject({ presence: null, state: null, online: false });
  });
});
