import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { LocalEdgeStatus } from "@/api/local-edge";
import { Pi5LocalPanel, numberText } from "../Pi5LocalPanel";

const mock = vi.hoisted(() => ({
  data: {} as LocalEdgeStatus,
  refetch: vi.fn().mockResolvedValue({}),
  command: vi.fn().mockResolvedValue({ state: "queued" }),
  bind: vi.fn().mockResolvedValue({}),
  invalidate: vi.fn().mockResolvedValue({}),
}));
vi.mock("@/api/auth", () => ({ useAuth: () => ({ user: { service: "HOME", role: "USER" } }) }));
vi.mock("@/api/local-edge", () => ({
  useLocalEdgeStatus: () => ({ data: mock.data, refetch: mock.refetch }),
  localEdgeCommand: mock.command,
  bindLocalEdge: mock.bind,
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: mock.invalidate }),
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

beforeEach(() => {
  vi.clearAllMocks();
  mock.data = {
    enabled: true,
    device_id: "sensor",
    agent_available: true,
    agent: { edge_state: "active" },
    runtime_current: true,
    calibration: { phase: "waiting_empty_room", run_id: "fresh-run" },
  };
});
afterEach(cleanup);

describe("Pi5 실제 제어와 측정 상태", () => {
  it("uses current run confirmation, not random/demo calibration", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Pi5LocalPanel deviceId="sensor" />);
    fireEvent.click(screen.getByText("빈방 확인 · 측정 시작"));
    await waitFor(() =>
      expect(mock.command).toHaveBeenCalledWith("sensor", "confirm-empty-room", "fresh-run"),
    );
    expect(screen.getByText(/SPI 펌웨어 AGC: 미지원/)).toBeTruthy();
  });
  it("keeps confirmation disabled for stale runtime", () => {
    mock.data.runtime_current = false;
    render(<Pi5LocalPanel />);
    expect((screen.getByText("빈방 확인 · 측정 시작") as HTMLButtonElement).disabled).toBe(true);
  });
  it("does not claim settings were applied before runtime acknowledgement", () => {
    mock.data.desired_revision = "new-config";
    mock.data.runtime = {
      pi5_services: { runtime_configuration: { applied_revision: "old-config" } },
    };
    render(<Pi5LocalPanel controls={false} />);
    expect(screen.getByText(/실행 코드 적용 대기/)).toBeTruthy();
  });
  it("shows missing total as unmeasured, not 0 percent", () => {
    mock.data.resources = {
      fresh: true,
      complete: false,
      errors: { database: "offline" },
      scope: "stack",
      groups: {},
      total: { cpu_percent_host_basis: null, memory_mib: null, swap_mib: null },
      system: { ram_available_mib: 7000, temperature_c: 50, disk_used_gib: 8, disk_free_gib: 20 },
    };
    render(<Pi5LocalPanel controls={false} />);
    expect(screen.getByText("전체 CPU 미측정")).toBeTruthy();
    expect(screen.getByText("전체 메모리 미측정")).toBeTruthy();
    expect(screen.getByText(/필수 서비스 일부 미동작/)).toBeTruthy();
  });
  it("binding requires selected SPI device and stopped reader", async () => {
    mock.data.device_id = null;
    mock.data.agent = { edge_state: "inactive" };
    render(<Pi5LocalPanel deviceId="new-sensor" canBind />);
    fireEvent.click(screen.getByText("선택한 SPI 장치를 이 Pi5에 연결"));
    await waitFor(() => expect(mock.bind).toHaveBeenCalledWith("new-sensor"));
  });
  it("original non-local profile has no control panel", () => {
    mock.data.enabled = false;
    const view = render(<Pi5LocalPanel />);
    expect(view.container.innerHTML).toBe("");
  });
  it("does not render NaN as a measured value", () => {
    expect(numberText(Number.NaN)).toBe("미측정");
    expect(numberText(0, "%")).toBe("0.00%");
  });
  it("keeps the compact user view free of resource and protocol details", () => {
    render(<Pi5LocalPanel compact />);
    expect(screen.getByRole("button", { name: "모니터링 시작" })).toBeTruthy();
    expect(screen.queryByText(/SPI 펌웨어 AGC|전체 CPU|처리 P95|추론 게이트/)).toBeNull();
  });
  it("preserves the current calibration run and confirmation in the compact view", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<Pi5LocalPanel compact />);
    fireEvent.click(screen.getByRole("button", { name: "빈방 확인 · 보정 시작" }));
    expect(mock.command).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "빈방 확인 · 보정 시작" }));
    await waitFor(() =>
      expect(mock.command).toHaveBeenCalledWith("sensor", "confirm-empty-room", "fresh-run"),
    );
  });
  it("does not bypass the stale-runtime guard in the compact view", () => {
    mock.data.runtime_current = false;
    render(<Pi5LocalPanel compact />);
    expect(
      (screen.getByRole("button", { name: "빈방 확인 · 보정 시작" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
