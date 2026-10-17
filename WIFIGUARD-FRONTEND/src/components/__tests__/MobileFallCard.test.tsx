import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { FallEvent } from "@/lib/domain";
import { MobileFallCard } from "../MobileFallCard";

afterEach(cleanup);
const fall = {
  id: "persisted-event",
  residentName: "가족 1",
  room: "거실",
  occurredAt: "2026-10-13T03:00:00Z",
  confidence: 0.82,
  durationS: 1.2,
  response: "PENDING",
  respondedAt: null,
} as FallEvent;
function mount(overrides = {}) {
  const onRespond = vi.fn(),
    onToggle = vi.fn(),
    onOpen = vi.fn();
  render(
    <MobileFallCard
      fall={fall}
      residentName="가족 1"
      roomName="거실"
      open={false}
      busy={false}
      onRespond={onRespond}
      onToggle={onToggle}
      onOpen={onOpen}
      {...overrides}
    />,
  );
  return { onRespond, onToggle, onOpen };
}
it("reading an alert does not acknowledge it automatically", () => {
  const { onRespond } = mount();
  expect(screen.getByText("대기중", { selector: ".response-badge" })).toBeTruthy();
  expect(onRespond).not.toHaveBeenCalled();
});
it("explicit acknowledgement preserves the existing response contract", () => {
  const { onRespond } = mount();
  fireEvent.click(screen.getByRole("button", { name: "확인했어요" }));
  expect(onRespond).toHaveBeenCalledWith("ACKNOWLEDGED");
});
it("opens a pending saved alert without recording a response", () => {
  const { onOpen, onRespond } = mount();
  fireEvent.click(screen.getByRole("button", { name: "낙상 알림 크게 보기" }));
  expect(onOpen).toHaveBeenCalledOnce();
  expect(onRespond).not.toHaveBeenCalled();
});
it("disables response writes while a mutation is pending", () => {
  mount({ busy: true });
  expect((screen.getByRole("button", { name: "확인했어요" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
});
it("exposes saved facts only when details are expanded", () => {
  mount({ open: true });
  expect(screen.getByText("82.0%")).toBeTruthy();
  expect(screen.getByText(/저장된 움직임 기록이 없습니다/)).toBeTruthy();
});
it("keeps all response options without presenting old alerts as pending", () => {
  mount({ fall: { ...fall, response: "ACKNOWLEDGED" } });
  expect(screen.queryByRole("button", { name: "확인했어요" })).toBeNull();
  const select = screen.getByRole("combobox") as HTMLSelectElement;
  expect(select.value).toBe("ACKNOWLEDGED");
  expect([...select.options].map((o) => o.value)).toEqual([
    "PENDING",
    "ACKNOWLEDGED",
    "DISPATCHED",
    "FALSE_ALARM",
  ]);
});
