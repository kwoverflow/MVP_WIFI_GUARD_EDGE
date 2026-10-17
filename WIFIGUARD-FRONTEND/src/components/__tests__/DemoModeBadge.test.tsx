import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { DemoModeBadge } from "../DemoModeBadge";

vi.mock("@/api/local-edge", () => ({ useLocalEdgeStatus: () => ({ data: undefined }) }));
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});
it("discloses the demo source even before metadata arrives", () => {
  vi.stubEnv("VITE_PC_SIMULATION", "1");
  render(<DemoModeBadge />);
  expect(screen.getByText("데모 모드")).toBeTruthy();
  expect(screen.getByTitle(/외부 알림은 발송되지 않습니다/)).toBeTruthy();
});
it("does not add demo branding to production", () => {
  vi.stubEnv("VITE_PC_SIMULATION", "0");
  vi.stubEnv("VITE_USE_MOCK", "0");
  const view = render(<DemoModeBadge />);
  expect(view.container.innerHTML).toBe("");
});
