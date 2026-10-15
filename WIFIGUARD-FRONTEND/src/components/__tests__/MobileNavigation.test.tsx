import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
const fixture = vi.hoisted(() => ({
  pathname: "/history",
  count: undefined as number | undefined,
  error: false,
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, ...props }: { to: string; children: ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useRouterState: () => fixture.pathname,
}));
vi.mock("@/api/queries", () => ({
  useFalls: () => ({
    data: fixture.count == null ? undefined : { total: fixture.count },
    isError: fixture.error,
  }),
}));
import { MobileNavigation } from "../MobileNavigation";
afterEach(() => {
  cleanup();
  fixture.count = undefined;
  fixture.error = false;
});
it("navigates to the same authenticated desktop routes", () => {
  render(<MobileNavigation />);
  expect(screen.getByRole("link", { name: "현황" }).getAttribute("href")).toBe("/");
  expect(screen.getByRole("link", { name: "장치" }).getAttribute("href")).toBe("/devices");
  expect(screen.getByRole("link", { name: "알림" }).getAttribute("aria-current")).toBe("page");
});
it("does not invent a zero count when notifications have not loaded", () => {
  render(<MobileNavigation />);
  expect(screen.queryByLabelText(/확인 대기 알림/)).toBeNull();
});
it("shows the server pending count, not the total history count", () => {
  fixture.count = 3;
  render(<MobileNavigation />);
  expect(screen.getByLabelText("확인 대기 알림 3건").textContent).toBe("3");
});
it("hides an old notification count when the query failed", () => {
  fixture.count = 3;
  fixture.error = true;
  render(<MobileNavigation />);
  expect(screen.queryByLabelText(/확인 대기 알림/)).toBeNull();
});
