/**
 * 서비스 API fetch 래퍼 — 베이스 URL(env), Bearer 헤더, 401 → refresh 1회 → 재시도 → 실패 시 로그아웃.
 *
 * - access 토큰은 메모리에만, refresh 토큰은 localStorage["csi-guard-refresh"] 에 둔다 (SSR 에서는 둘 다 없음).
 * - 오류는 백엔드 포맷 {detail, code, fields?} 를 ApiError 로 던진다 (services/api errors.py).
 */

export const API_BASE = (import.meta.env.VITE_PI5_LOCAL === "1" ? "" : (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000")).replace(
  /\/$/,
  "",
);
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";
export const API_V1 = "/api/v1";

const REFRESH_KEY = "csi-guard-refresh";
const isBrowser = () => typeof window !== "undefined";

export class ApiError extends Error {
  status: number;
  code: string;
  fields?: Record<string, unknown>;
  constructor(status: number, code: string, detail: string, fields?: Record<string, unknown>) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

// ── 토큰 보관 ────────────────────────────────────────────────────────
let accessToken: string | null = null;

export const tokens = {
  getAccess: () => accessToken,
  setAccess(token: string | null) {
    accessToken = token;
  },
  getRefresh(): string | null {
    if (!isBrowser()) return null;
    try {
      return window.localStorage.getItem(REFRESH_KEY);
    } catch {
      return null;
    }
  },
  setRefresh(token: string | null) {
    if (!isBrowser()) return;
    try {
      if (token) window.localStorage.setItem(REFRESH_KEY, token);
      else window.localStorage.removeItem(REFRESH_KEY);
    } catch {
      /* private mode 등 */
    }
  },
  clear() {
    accessToken = null;
    tokens.setRefresh(null);
  },
};

export interface TokenPairLike {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}

/** 로그인/refresh/비밀번호 변경 응답을 받아 두 토큰을 저장한다. */
export function storeTokenPair(pair: TokenPairLike) {
  tokens.setAccess(pair.accessToken);
  tokens.setRefresh(pair.refreshToken);
}

// ── 401 처리 ────────────────────────────────────────────────────────
let unauthorizedHandler: (() => void) | null = null;
/** auth.ts 가 등록한다 — refresh 까지 실패했을 때 호출(로그아웃). */
export function setUnauthorizedHandler(fn: (() => void) | null) {
  unauthorizedHandler = fn;
}

let refreshing: Promise<boolean> | null = null;

/** 저장된 refresh 로 새 토큰 쌍을 받는다. 동시 호출은 하나로 합쳐진다. 실패 시 토큰을 지우고 false. */
export function refreshSession(): Promise<boolean> {
  if (refreshing) return refreshing;
  const refresh = tokens.getRefresh();
  if (!refresh) return Promise.resolve(false);
  const run = (async () => {
    try {
      const res = await fetch(`${API_BASE}${API_V1}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refreshToken: refresh }),
      });
      if (!res.ok) {
        tokens.clear();
        return false;
      }
      storeTokenPair((await res.json()) as TokenPairLike);
      return true;
    } catch {
      return false;
    }
  })();
  // 완료 후에만 비운다 (동기 조기 반환이 있으면 대입 순서 때문에 오래된 프로미스가 남는다).
  refreshing = run.finally(() => {
    refreshing = null;
  });
  return refreshing;
}

// ── fetch ───────────────────────────────────────────────────────────
export type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Query;
  /** false 면 Authorization 헤더를 붙이지 않는다 (로그인/가입). */
  auth?: boolean;
  signal?: AbortSignal;
  timeoutMs?: number;
}

function buildUrl(path: string, query?: Query): string {
  const url = path.startsWith("http")
    ? path
    : `${API_BASE}${path.startsWith("/api") ? "" : API_V1}${path}`;
  if (!query) return url;
  const qs = Object.entries(query)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
  return qs ? `${url}?${qs}` : url;
}

async function parseError(res: Response): Promise<ApiError> {
  let detail = `${res.status} ${res.statusText}`;
  let code = "HTTP_ERROR";
  let fields: Record<string, unknown> | undefined;
  try {
    const data = (await res.json()) as {
      detail?: unknown;
      code?: string;
      fields?: Record<string, unknown>;
    };
    if (typeof data.detail === "string") detail = data.detail;
    if (data.code) code = data.code;
    fields = data.fields;
  } catch {
    /* 본문 없음 */
  }
  return new ApiError(res.status, code, detail, fields);
}

/**
 * `apiFetch<DeviceOut[]>("/devices")` — 경로는 /api/v1 기준(접두 생략 가능). 204 는 undefined 를 돌려준다.
 */
export async function apiFetch<T>(
  path: string,
  opts: RequestOptions = {},
  _retried = false,
): Promise<T> {
  const { method = "GET", body, query, auth = true, signal, timeoutMs = 8000 } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const access = tokens.getAccess();
  if (auth && access) headers.Authorization = `Bearer ${access}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  signal?.addEventListener("abort", () => controller.abort());
  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 401 && auth) {
    if (!_retried && (await refreshSession())) {
      return apiFetch<T>(path, opts, true);
    }
    tokens.clear();
    unauthorizedHandler?.();
    throw await parseError(res);
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
