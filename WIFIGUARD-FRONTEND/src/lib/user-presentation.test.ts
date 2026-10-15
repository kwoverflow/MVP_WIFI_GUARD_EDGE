import { expect, it } from "vitest";
import { userLabel, activityMessage } from "./user-presentation";

it("aliases built-in demo fixture labels only", () => {
  expect(userLabel("모의 시험방", "room", true)).toBe("거실");
  expect(userLabel("모의 시험 대상", "resident", true)).toBe("대상자 1");
  expect(userLabel("PC 모의 ESP32-C5", "device", true)).toBe("거실 센서");
});
it("preserves the exact production label even when it matches a demo name", () => {
  expect(userLabel("모의 시험방", "room", false)).toBe("모의 시험방");
});
it("never rewrites ordinary user-provided names", () => {
  expect(userLabel("김영희", "resident", true)).toBe("김영희");
  expect(userLabel("침실", "room", true)).toBe("침실");
});
it("does not mutate source records while simplifying their display", () => {
  const original = { room: "모의 시험방" };
  userLabel(original.room, "room", true);
  expect(original.room).toBe("모의 시험방");
});
it("translates known activity descriptions without exposing runtime ids", () => {
  expect(activityMessage("PC 모의 RAW 기준값 보정: done · run-123", true)).toBe("빈방 보정 완료");
  expect(activityMessage("Pi5 명령 접수: start", true)).toBe("모니터링 시작 요청");
});
it("does not hide unfamiliar errors or rewrite production activity", () => {
  const error = "장치 오류: connection refused";
  expect(activityMessage(error, true)).toBe(error);
  expect(activityMessage("Pi5 명령 접수: start", false)).toBe("Pi5 명령 접수: start");
});
