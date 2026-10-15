import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  refreshSession,
  setUnauthorizedHandler,
  storeTokenPair,
  tokens,
} from "../client";

type FetchMock = ReturnType<typeof vi.fn>;

function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  tokens.clear();
  setUnauthorizedHandler(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiFetch", () => {
  it("GET 은 /api/v1 접두와 Bearer 헤더를 붙이고 JSON 을 돌려준다", async () => {
    tokens.setAccess("acc");
    fetchMock.mockResolvedValueOnce(jsonRes(200, [{ id: "d1" }]));
    const out = await apiFetch<{ id: string }[]>("/devices", { query: { limit: 5, q: "" } });
    expect(out).toEqual([{ id: "d1" }]);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/v1/devices?limit=5");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer acc");
    expect(init.method).toBe("GET");
  });

  it("오류 본문 {detail, code, fields} 를 ApiError 로 던진다", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(409, { detail: "이미 사용 중", code: "EMAIL_TAKEN", fields: { email: "x" } }),
    );
    await expect(
      apiFetch("/account", { method: "PATCH", body: { email: "a" } }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      code: "EMAIL_TAKEN",
      message: "이미 사용 중",
      fields: { email: "x" },
    });
  });

  it("204 는 undefined 를 돌려준다", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(apiFetch("/devices/x", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("401 이면 refresh 로 새 토큰을 받아 한 번 재시도한다", async () => {
    tokens.setAccess("old");
    tokens.setRefresh("r1");
    fetchMock
      .mockResolvedValueOnce(jsonRes(401, { detail: "expired", code: "TOKEN_EXPIRED" }))
      .mockResolvedValueOnce(
        jsonRes(200, { accessToken: "new", refreshToken: "r2", expiresIn: 900 }),
      )
      .mockResolvedValueOnce(jsonRes(200, { ok: true }));
    const out = await apiFetch<{ ok: boolean }>("/auth/me");
    expect(out).toEqual({ ok: true });
    expect(tokens.getAccess()).toBe("new");
    expect(tokens.getRefresh()).toBe("r2");
    const lastInit = fetchMock.mock.calls[2][1] as RequestInit;
    expect((lastInit.headers as Record<string, string>).Authorization).toBe("Bearer new");
  });

  it("refresh 까지 실패하면 토큰을 지우고 로그아웃 핸들러를 부른다", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    tokens.setAccess("old");
    tokens.setRefresh("r1");
    fetchMock
      .mockResolvedValueOnce(jsonRes(401, { detail: "expired", code: "TOKEN_EXPIRED" }))
      .mockResolvedValueOnce(jsonRes(401, { detail: "reused", code: "REFRESH_REUSED" }));
    await expect(apiFetch("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(tokens.getAccess()).toBeNull();
    expect(tokens.getRefresh()).toBeNull();
  });

  it("auth:false 요청은 401 이라도 refresh 를 시도하지 않는다", async () => {
    tokens.setRefresh("r1");
    fetchMock.mockResolvedValueOnce(jsonRes(401, { detail: "bad", code: "INVALID_CREDENTIALS" }));
    await expect(
      apiFetch("/auth/login", { method: "POST", body: {}, auth: false }),
    ).rejects.toMatchObject({ code: "INVALID_CREDENTIALS" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("refreshSession", () => {
  it("refresh 토큰이 없으면 false", async () => {
    await expect(refreshSession()).resolves.toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("동시 호출은 하나의 요청으로 합쳐진다", async () => {
    storeTokenPair({ accessToken: "a", refreshToken: "r", expiresIn: 1 });
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { accessToken: "a2", refreshToken: "r2", expiresIn: 900 }),
    );
    const [x, y] = await Promise.all([refreshSession(), refreshSession()]);
    expect(x && y).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(tokens.getRefresh()).toBe("r2");
  });
});
