import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { FallEvent } from "@/lib/domain";
import { FallHistoryDetails } from "../FallHistoryDetails";

afterEach(cleanup);
const fall = {
  occurredAt: "2026-10-13T03:00:00Z",
  confidence: 0.82,
  durationS: 1.2,
  response: "PENDING",
  respondedAt: null,
} as FallEvent;
it("renders saved event facts without fabricated waveform or state timeline", () => {
  const view = render(<FallHistoryDetails fall={fall} />);
  expect(screen.getByText("82.0%")).toBeTruthy();
  expect(screen.getByText("1.20초")).toBeTruthy();
  expect(screen.getByText(/저장된 움직임 기록이 없습니다/)).toBeTruthy();
  expect(view.container.querySelector("svg")).toBeNull();
  expect(screen.queryByText(/Peak|State Timeline|Threshold/)).toBeNull();
});
it("does not invent a response timestamp", () => {
  render(<FallHistoryDetails fall={fall} />);
  expect(screen.getByText("미처리")).toBeTruthy();
});
