import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { LocalEdgeStatus } from "@/api/local-edge";
import { PcSimulationPanel } from "../PcSimulationPanel";

const mock = vi.hoisted(() => ({ data: {} as LocalEdgeStatus, refetch: vi.fn(), scenario: vi.fn(), alert: vi.fn(), command: vi.fn(), invalidate: vi.fn() }));
vi.mock("@/api/local-edge", () => ({ useLocalEdgeStatus: () => ({ data: mock.data, refetch: mock.refetch }),
  setSimulationScenario: mock.scenario, testSimulationAlert: mock.alert, localEdgeCommand: mock.command }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ invalidateQueries: mock.invalidate }) }));
vi.mock("@tanstack/react-router", () => ({ Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a> }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
beforeEach(() => {
  vi.clearAllMocks();
  mock.data = { enabled: true, device_id: "pc-sensor", agent_available: true, runtime_current: true,
    calibration: { phase: "waiting_empty_room", run_id: "current-run" },
    simulation: { enabled: true, scenario: "empty", updated_at_unix: Date.now() / 1000, database_engine: "PostgreSQL",
      notification: { email_captured: 0, push_http_captured: 0, real_email_or_phone_delivery: false } } };
});
afterEach(cleanup);
it("is absent in operating Pi5 and original cloud UI", () => {
  mock.data.simulation = null;
  expect(render(<PcSimulationPanel />).container.innerHTML).toBe("");
});
it("cannot switch to motion before actual baseline finishes", () => {
  render(<PcSimulationPanel />);
  expect((screen.getByRole("button", { name: "움직임 움직임을 포함한 RAW 입력" }) as HTMLButtonElement).disabled).toBe(true);
});
it("requires explicit nonce-scoped calibration confirmation", async () => {
  render(<PcSimulationPanel />);
  fireEvent.click(screen.getByText("빈방 보정 시작 · 약 60초"));
  expect(mock.command).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("시험 실행"));
  await waitFor(() => expect(mock.command).toHaveBeenCalledWith("pc-sensor", "confirm-empty-room", "current-run"));
});
it("labels injected alerts and requires a separate confirmation", async () => {
  render(<PcSimulationPanel />);
  fireEvent.click(screen.getByText("알림 경로 시험 · 이벤트 주입"));
  expect(screen.getByText(/모델의 낙상 판정이 아니며/)).toBeTruthy();
  expect(mock.alert).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("시험 실행"));
  await waitFor(() => expect(mock.alert).toHaveBeenCalledOnce());
});
it("stale supervisor cannot accept controls", () => {
  mock.data.simulation!.updated_at_unix -= 20;
  render(<PcSimulationPanel />);
  expect((screen.getByText("모의 수신 시작") as HTMLButtonElement).disabled).toBe(true);
});
