import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { LocalEdgeStatus } from "@/api/local-edge";
import type { DeviceLive } from "@/api/realtime/types";
import { MonitoringDashboard } from "../MonitoringDashboard";

const mock = vi.hoisted(() => ({
  local: {} as LocalEdgeStatus,
  live: null as DeviceLive | null,
  detection: "INACTIVE",
  service: "HOME",
  selected: null as string | null,
  pendingError: false,
  pendingTotal: 2,
  series: [] as { t: number; mv: number }[],
}));
vi.mock("@/api/auth", () => ({
  useAuth: () => ({ user: { service: mock.service }, features: { fallSimulate: false } }),
}));
vi.mock("@/api/local-edge", () => ({
  useLocalEdgeStatus: () => ({ data: mock.local, isError: false }),
}));
vi.mock("@/api/queries", () => ({
  useDetectionStatus: () => mock.detection,
  useConfig: () => ({ data: { presenceMvThreshold: 2 } }),
  useDevices: () => ({
    data: [
      { id: "d1", name: "sensor", room: "침실", calibrationStage: "IDLE" },
      { id: "d2", name: "unmapped", room: "거실", calibrationStage: "IDLE" },
    ],
  }),
  useResidents: () => ({
    data: [
      {
        id: "r1",
        name: "홍길동",
        room: "침실",
        deviceId: "d1",
        deviceIds: ["d1"],
        state: null,
        presence: null,
      },
    ],
  }),
  useMvHistory: () => mock.series,
  useFalls: (params: { response?: string }) =>
    params.response
      ? {
          data: mock.pendingError ? undefined : { items: [], total: mock.pendingTotal },
          isError: mock.pendingError,
        }
      : { data: { items: [], total: 0 }, isLoading: false, isError: false },
}));
vi.mock("@/api/realtime/useLiveStream", () => ({
  useLiveDevice: (id: string) => {
    mock.selected = id;
    return mock.live;
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, ...props }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));
vi.mock("../MonitoringHeader", () => ({
  Header: ({ title }: { title: string }) => <h2>{title}</h2>,
  ResponseBadge: () => null,
}));
vi.mock("../Pi5LocalPanel", () => ({ Pi5LocalPanel: () => <p>Local diagnostics</p> }));
vi.mock("../PcSimulationPanel", () => ({ PcSimulationPanel: () => <p>PC controls</p> }));
vi.mock("../DiagnosticFallControl", () => ({ DiagnosticFallControl: () => null }));
vi.mock("../EventLogPanel", () => ({ EventLogPanel: () => <p>Event logs</p> }));
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  mock.local = {
    enabled: true,
    device_id: "d1",
    agent_available: true,
    runtime_current: false,
    simulation: {
      enabled: true,
      scenario: "empty",
      updated_at_unix: Date.now() / 1000,
      database_engine: "PostgreSQL",
      notification: {
        email_captured: 0,
        push_http_captured: 0,
        real_email_or_phone_delivery: false,
      },
    },
  };
  mock.live = null;
  mock.detection = "INACTIVE";
  mock.service = "HOME";
  mock.pendingError = false;
  mock.pendingTotal = 2;
  mock.series = [];
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows user status without developer panels or promotional filler", () => {
  const { container } = render(<MonitoringDashboard />);
  expect(screen.getByText("판단 불가")).toBeTruthy();
  expect(screen.getByText("감지 중지")).toBeTruthy();
  expect(container.querySelector("details")).toBeNull();
  expect(screen.queryByText("공간의 상태를 한눈에")).toBeNull();
  expect(
    screen.queryByText(/PC 모의검증|전체 서비스 CPU|Local diagnostics|PC controls/),
  ).toBeNull();
});
it("shows the installation, calibration, resident and notification guide on request", () => {
  render(<MonitoringDashboard />);
  fireEvent.click(screen.getByRole("button", { name: /사용 안내/ }));
  expect(screen.getByRole("region", { name: "처음 사용 안내" })).toBeTruthy();
  expect(screen.getByText("빈방 보정")).toBeTruthy();
  expect(screen.getByText("대상자 등록")).toBeTruthy();
});
it("does not claim no alerts when the history request fails", () => {
  mock.pendingError = true;
  render(<MonitoringDashboard />);
  expect(screen.getByText("조회 실패")).toBeTruthy();
  expect(screen.queryByText("확인 대기 알림이 없습니다")).toBeNull();
});
it("directs setup to device management instead of embedding test controls", () => {
  render(<MonitoringDashboard />);
  const links = screen.getAllByRole("link", { name: /장치 관리/ });
  expect(links.every((link) => link.getAttribute("href") === "/devices")).toBe(true);
  expect(screen.queryByText(/모의 수신 시작|알림 경로 시험/)).toBeNull();
});
it("selects an unmapped device without assigning the first resident to it", () => {
  render(<MonitoringDashboard />);
  fireEvent.change(screen.getByRole("combobox", { name: "모니터링 장치" }), {
    target: { value: "d2" },
  });
  expect(mock.selected).toBe("d2");
  expect(screen.getByText("대상자 미등록")).toBeTruthy();
});
it("does not use changing device heartbeats as the last sensor evidence timestamp", () => {
  mock.live = {
    device_id: "d1",
    online: false,
    last_seen_at: "2026-10-13T04:00:00Z",
    link: null,
    presence: null,
    fall: null,
  };
  const { rerender } = render(<MonitoringDashboard />);
  expect(screen.getByText("센서 연결을 기다리고 있습니다")).toBeTruthy();
  mock.series = [{ t: Date.parse("2026-10-13T03:00:00Z"), mv: 1 }];
  rerender(<MonitoringDashboard />);
  const evidence = screen.getByText(/마지막 수신/).textContent;
  mock.live.last_seen_at = "2026-10-13T05:00:00Z";
  rerender(<MonitoringDashboard />);
  expect(screen.getByText(/마지막 수신/).textContent).toBe(evidence);
});
