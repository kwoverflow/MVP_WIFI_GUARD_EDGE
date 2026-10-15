import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authStore } from "../auth";
import { tokens } from "../client";

function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ME = {
  id: "u1",
  email: "home@test.io",
  name: "홈",
  service: "HOME",
  role: "USER",
  facilityId: null,
  onboarded: true,
  createdAt: "2026-09-07T00:00:00Z",
  facility: null,
  features: { fallSimulate: true },
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  authStore._reset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function flush() {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

describe("authStore bootstrap", () => {
  it("refresh 토큰이 없으면 anon", async () => {
    const unsub = authStore.subscribe(() => {});
    await flush();
    expect(authStore.get().status).toBe("anon");
    expect(fetchMock).not.toHaveBeenCalled();
    unsub();
  });

  it("refresh 토큰이 있으면 /auth/refresh → /auth/me 로 세션을 복원한다", async () => {
    tokens.setRefresh("r1");
    fetchMock
      .mockResolvedValueOnce(jsonRes(200, { accessToken: "a", refreshToken: "r2", expiresIn: 900 }))
      .mockResolvedValueOnce(jsonRes(200, ME));
    const unsub = authStore.subscribe(() => {});
    await flush();
    const s = authStore.get();
    expect(s.status).toBe("authed");
    expect(s.user?.email).toBe("home@test.io");
    expect(s.facility).toBeNull();
    expect(s.features.fallSimulate).toBe(true);
    unsub();
  });

  it("refresh 가 거부되면 anon 이고 토큰이 지워진다", async () => {
    tokens.setRefresh("dead");
    fetchMock.mockResolvedValueOnce(jsonRes(401, { detail: "x", code: "REFRESH_INVALID" }));
    const unsub = authStore.subscribe(() => {});
    await flush();
    expect(authStore.get().status).toBe("anon");
    expect(tokens.getRefresh()).toBeNull();
    unsub();
  });
});

describe("authStore login/logout", () => {
  it("login 은 토큰을 저장하고 me 를 읽는다", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonRes(200, { accessToken: "a", refreshToken: "r", expiresIn: 900, user: ME }),
      )
      .mockResolvedValueOnce(jsonRes(200, ME));
    await authStore.login("home@test.io", "password8");
    expect(authStore.get().status).toBe("authed");
    expect(tokens.getAccess()).toBe("a");
    const loginInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect((loginInit.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("logout 은 서버 호출 실패와 무관하게 로컬 세션을 지운다", async () => {
    tokens.setAccess("a");
    tokens.setRefresh("r");
    fetchMock.mockRejectedValueOnce(new Error("network down"));
    await authStore.logout();
    expect(authStore.get().status).toBe("anon");
    expect(tokens.getAccess()).toBeNull();
    expect(tokens.getRefresh()).toBeNull();
  });
});
