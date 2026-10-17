import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Pi5WifiPanel } from "../Pi5WifiPanel";
import { ApiError } from "@/api/client";

const mocks = vi.hoisted(() => ({ status: vi.fn(), auth: vi.fn(), connect: vi.fn(), scan: vi.fn(), finish: vi.fn(), refetch: vi.fn() }));
vi.mock("@/api/auth", () => ({ useAuth: mocks.auth }));
vi.mock("@/api/local-network", () => ({ useWifiStatus: mocks.status, connectWifi: mocks.connect, scanWifi: mocks.scan, finishWifi: mocks.finish }));
const base = { enabled: true, available: true, secure_credentials_transport: true,
  interfaces: [{ name: "wlan0", connected: true, addresses: ["192.0.2.9"] }, { name: "eth0", connected: true, addresses: ["192.0.2.8"] }],
  ssid: "우리집", autoconnect: false, edge_stopped: true, updated_at_unix: Date.now() / 1000, transaction: null };
beforeEach(() => {
  vi.clearAllMocks();
  mocks.auth.mockReturnValue({ user: { id: "owner", service: "HOME", role: "USER" } });
  mocks.status.mockReturnValue({ data: { ...base, updated_at_unix: Date.now() / 1000 }, refetch: mocks.refetch, isError: false });
  mocks.refetch.mockResolvedValue({});
});
afterEach(cleanup);

describe("Pi5 Wi-Fi GUI", () => {
  it("explains ownership failures instead of silently hiding Wi-Fi", () => {
    mocks.status.mockReturnValue({ data: undefined, isError: true, error: new ApiError(404, "NOT_FOUND", "장치 없음") });
    render(<Pi5WifiPanel />);
    expect(screen.getByRole("alert").textContent).toContain("수신기를 연결한 계정");
    expect(screen.queryByLabelText("공유기 비밀번호")).toBeNull();
  });
  it("hides the panel in non-Pi5 installations", () => {
    mocks.status.mockReturnValue({ data: { enabled: false } });
    const { container } = render(<Pi5WifiPanel />);
    expect(container.textContent).toBe("");
  });
  it("does not accept a router password on ordinary HTTP", () => {
    mocks.status.mockReturnValue({ data: { ...base, secure_credentials_transport: false }, refetch: mocks.refetch });
    render(<Pi5WifiPanel />);
    expect(screen.queryByLabelText("공유기 비밀번호")).toBeNull();
    expect(screen.getByText(/일반 HTTP 화면/)).toBeTruthy();
  });
  it("clears passwords immediately and requires explicit saving", async () => {
    const tx = { id: "test-tx", phase: "awaiting_confirmation", deadline: Date.now() / 1000 + 110 };
    mocks.connect.mockResolvedValue({ transaction: tx });
    render(<Pi5WifiPanel />);
    fireEvent.change(screen.getByLabelText("Wi-Fi 이름"), { target: { value: "우리집" } });
    fireEvent.change(screen.getByLabelText("공유기 비밀번호"), { target: { value: "ONLY-TEST-SECRET" } });
    fireEvent.click(screen.getByRole("button", { name: "임시 연결하기" }));
    await waitFor(() => expect(mocks.connect).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole("button", { name: "연결 확인 · 저장" })).toBeTruthy());
    expect(screen.queryByDisplayValue("ONLY-TEST-SECRET")).toBeNull();
    expect(mocks.finish).not.toHaveBeenCalled();
    mocks.finish.mockResolvedValue({ transaction: { ...tx, phase: "committed" } });
    fireEvent.click(screen.getByRole("button", { name: "연결 확인 · 저장" }));
    await waitFor(() => expect(mocks.finish).toHaveBeenCalledWith("confirm", "test-tx"));
  });
  it("blocks changing networks during sensor capture", () => {
    mocks.status.mockReturnValue({ data: { ...base, edge_stopped: false }, refetch: mocks.refetch });
    render(<Pi5WifiPanel />);
    expect((screen.getByRole("button", { name: "주변 Wi-Fi 검색" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("공유기 비밀번호") as HTMLInputElement).disabled).toBe(true);
  });
  it("does not enable operations from stale or offline data", () => {
    mocks.status.mockReturnValue({ data: base, refetch: mocks.refetch, isError: true });
    render(<Pi5WifiPanel />);
    expect((screen.getByRole("button", { name: "임시 연결하기" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("이전 상태");
  });
  it("does not provide controls to a facility member", () => {
    mocks.auth.mockReturnValue({ user: { id: "member", service: "FACILITY", role: "MEMBER" } });
    render(<Pi5WifiPanel />);
    expect(screen.queryByRole("button", { name: "주변 Wi-Fi 검색" })).toBeNull();
    expect(screen.queryByLabelText("공유기 비밀번호")).toBeNull();
  });
  it("permits rollback without saving a trial network", async () => {
    const tx = { id: "pending", phase: "awaiting_confirmation", deadline: Date.now() / 1000 + 100 };
    mocks.status.mockReturnValue({ data: { ...base, transaction: tx }, refetch: mocks.refetch });
    mocks.finish.mockResolvedValue({ transaction: { ...tx, phase: "rolled_back" } });
    render(<Pi5WifiPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "되돌리기" }));
    await waitFor(() => expect(mocks.finish).toHaveBeenCalledWith("rollback", "pending"));
  });
});
