/**
 * `liveSocket` 동작 — 구 구현이 갖고 있던 세 결함을 각각 고정한다.
 *
 *   1. 기기 개념 없음      → 기기별로 최신값·시계열이 따로 쌓이는지
 *   2. 고정 2초 재연결     → 지수 백오프가 실제로 커지는지
 *   3. JSON.parse 무방비   → 깨진 프레임이 스트림을 끊지 않는지
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { liveSocket, __testing } from "@/api/realtime/socket";

// ── WebSocket 목 ─────────────────────────────────────────────────────
class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }
  // 테스트가 서버 역할을 한다
  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
  deliver(obj: unknown) {
    this.onmessage?.({ data: typeof obj === "string" ? obj : JSON.stringify(obj) });
  }
  serverClose(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  get frames() {
    return this.sent.map((s) => JSON.parse(s));
  }
}

vi.mock("@/api/client", () => ({
  API_BASE: "http://test",
  tokens: { getAccess: () => "test-token" },
  refreshSession: vi.fn(async () => true),
}));

const HELLO = {
  type: "hello",
  protocol_version: 1,
  tenant_id: "home-1",
  user_id: "u1",
  device_ids: ["d1", "d2"],
  server_time: "2026-09-10T00:00:00Z",
};

function liveFor(deviceId: string, mv: number, state: "present" | "absent" = "present") {
  return {
    type: "live",
    server_time: "2026-09-10T00:00:01Z",
    devices: [
      {
        device_id: deviceId,
        online: true,
        presence: { state, mv_current: mv, updated_at: "2026-09-10T00:00:01Z" },
      },
    ],
  };
}

let unsubscribe: (() => void) | null = null;

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  liveSocket.__resetForTests();
});

afterEach(() => {
  unsubscribe?.();
  unsubscribe = null;
  liveSocket.__resetForTests();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function connect() {
  unsubscribe = liveSocket.subscribe(() => {});
  const ws = FakeWebSocket.instances.at(-1)!;
  ws.open();
  return ws;
}

describe("연결과 인증", () => {
  it("Pi5 same-origin 설정은 LAN 주소와 HTTPS에 맞는 소켓 URL을 만든다", () => {
    expect(__testing.websocketUrl("", "", "http://192.168.1.20:8080")).toBe(
      "ws://192.168.1.20:8080/ws/live",
    );
    expect(__testing.websocketUrl("", undefined, "https://guard.example")).toBe(
      "wss://guard.example/ws/live",
    );
    expect(__testing.websocketUrl("http://test/", "", "http://ignored")).toBe("ws://test/ws/live");
    expect(__testing.websocketUrl("", "wss://custom/ws", "http://ignored")).toBe("wss://custom/ws");
  });

  it("열리자마자 auth 를 보내고, hello 를 받으면 구독한다", () => {
    const ws = connect();
    expect(ws.frames[0]).toEqual({ type: "auth", token: "test-token" });

    ws.deliver(HELLO);
    expect(liveSocket.getSnapshot().status).toBe("open");
    expect(liveSocket.getSnapshot().deviceIds).toEqual(["d1", "d2"]);
    // 빈 목록 = 스코프 전체
    expect(ws.frames.at(-1)).toEqual({ type: "subscribe", device_ids: [] });
  });

  it("프로토콜 버전이 다르면 끊지 않되 눈에 보이게 남긴다", () => {
    const ws = connect();
    ws.deliver({ ...HELLO, protocol_version: 99 });
    expect(liveSocket.getSnapshot().status).toBe("open");
    expect(liveSocket.getSnapshot().lastError).toMatch(/프로토콜 버전 불일치/);
  });
});

describe("기기별 상태 (구 구현에는 없던 축)", () => {
  it("두 기기의 최신값과 시계열이 섞이지 않는다", () => {
    const ws = connect();
    ws.deliver(HELLO);
    ws.deliver(liveFor("d1", 1.5));
    ws.deliver(liveFor("d2", 4.2, "absent"));
    ws.deliver(liveFor("d1", 1.9));

    const snap = liveSocket.getSnapshot();
    expect(snap.devices.d1.presence!.mv_current).toBe(1.9);
    expect(snap.devices.d2.presence!.state).toBe("absent");
    expect(snap.history.d1.map((p) => p.mv)).toEqual([1.5, 1.9]);
    expect(snap.history.d2.map((p) => p.mv)).toEqual([4.2]);
  });

  it("시계열이 상한을 넘지 않는다", () => {
    const ws = connect();
    ws.deliver(HELLO);
    for (let i = 0; i < __testing.HISTORY_MAX + 20; i++) ws.deliver(liveFor("d1", i));

    const series = liveSocket.getSnapshot().history.d1;
    expect(series.length).toBe(__testing.HISTORY_MAX);
    expect(series.at(-1)!.mv).toBe(__testing.HISTORY_MAX + 19); // 최신이 남는다
  });

  it("mv 가 없는 갱신은 시계열에 쌓지 않는다", () => {
    const ws = connect();
    ws.deliver(HELLO);
    ws.deliver({
      type: "live",
      server_time: "t",
      devices: [{ device_id: "d1", online: true, presence: { state: "absent" } }],
    });
    expect(liveSocket.getSnapshot().history.d1).toBeUndefined();
    expect(liveSocket.getSnapshot().devices.d1).toBeDefined();
  });
});

describe("깨진 프레임 방어 (구 구현은 여기서 예외가 났다)", () => {
  it("파싱 실패를 세고 스트림은 유지한다", () => {
    const ws = connect();
    ws.deliver(HELLO);
    expect(() => ws.deliver("{{{ broken")).not.toThrow();
    expect(liveSocket.getSnapshot().droppedFrames).toBe(1);

    ws.deliver(liveFor("d1", 2.2)); // 다음 프레임은 정상 처리
    expect(liveSocket.getSnapshot().devices.d1.presence!.mv_current).toBe(2.2);
    expect(liveSocket.getSnapshot().status).toBe("open");
  });
});

describe("재연결 (구 구현은 고정 2초)", () => {
  it("연속 실패마다 대기가 길어진다", () => {
    connect();
    const delays: number[] = [];
    const spy = vi.spyOn(globalThis, "setTimeout");

    for (let i = 0; i < 4; i++) {
      const ws = FakeWebSocket.instances.at(-1)!;
      ws.serverClose(1006);
      const call = spy.mock.calls.at(-1);
      delays.push(call ? (call[1] as number) : 0);
      vi.advanceTimersByTime(60_000);
      FakeWebSocket.instances.at(-1)!.open();
    }

    expect(liveSocket.getSnapshot().status).not.toBe("idle");
    // 단조 증가(지터 때문에 엄밀한 배수는 아니다)하고 상한을 넘지 않는다
    expect(delays[1]).toBeGreaterThan(delays[0]);
    expect(delays[2]).toBeGreaterThan(delays[1]);
    expect(Math.max(...delays)).toBeLessThanOrEqual(__testing.BACKOFF_MAX_MS + 1_000);
    spy.mockRestore();
  });

  it("hello 를 받으면 백오프가 되감긴다", () => {
    connect();
    FakeWebSocket.instances.at(-1)!.serverClose(1006);
    vi.advanceTimersByTime(60_000);
    const ws2 = FakeWebSocket.instances.at(-1)!;
    ws2.open();
    ws2.deliver(HELLO); // 성공

    const spy = vi.spyOn(globalThis, "setTimeout");
    ws2.serverClose(1006);
    const delay = spy.mock.calls.at(-1)![1] as number;
    // 되감겼으니 첫 지연 수준(최소 + 지터)으로 돌아와야 한다
    expect(delay).toBeLessThanOrEqual(__testing.BACKOFF_MIN_MS + 1_000);
    spy.mockRestore();
  });

  it("마지막 구독자가 떠나면 연결을 닫는다", () => {
    const ws = connect();
    ws.deliver(HELLO);
    unsubscribe!();
    unsubscribe = null;
    expect(ws.readyState).toBe(3);
    expect(liveSocket.getSnapshot().status).toBe("idle");
  });
});
