/**
 * 인증 상태 — JWT access(메모리) + refresh(localStorage) 기반 외부 스토어와 useAuth().
 *
 * - 부팅: 브라우저에서 첫 구독 시 refresh 토큰이 있으면 /auth/refresh → /auth/me 로 세션을 복원한다.
 * - SSR: 항상 status "loading" (AuthGate 가 기존 hydrated 게이트와 같은 방식으로 로딩 화면을 그린다).
 * - 401 이 refresh 로도 해결되지 않으면 client.ts 가 등록된 핸들러(로그아웃)를 부른다.
 * - VITE_USE_MOCK=1 이면 mock-store 의 세션/계정을 그대로 쓴다 (demo 계정, 평문 비밀번호).
 */
import { useCallback, useEffect, useSyncExternalStore } from "react";

import type { Facility, Me, SignupInput, TokenPair, UserAccount } from "@/lib/domain";
import * as mock from "@/lib/mock-store";

import {
  ApiError,
  USE_MOCK,
  apiFetch,
  refreshSession,
  setUnauthorizedHandler,
  storeTokenPair,
  tokens,
} from "./client";

export type AuthStatus = "loading" | "authed" | "anon";

export interface AuthState {
  status: AuthStatus;
  user: UserAccount | null;
  facility: Facility | null;
  features: { fallSimulate: boolean };
}

const EMPTY: AuthState = {
  status: "loading",
  user: null,
  facility: null,
  features: { fallSimulate: false },
};
const ANON: AuthState = { ...EMPTY, status: "anon" };

let state: AuthState = EMPTY;
const listeners = new Set<() => void>();
let bootstrapped = false;

function set(next: AuthState) {
  state = next;
  for (const l of listeners) l();
}

function fromMe(me: Me): AuthState {
  const { facility, features, ...user } = me;
  return { status: "authed", user, facility: facility ?? null, features };
}

async function loadMe(): Promise<void> {
  const me = await apiFetch<Me>("/auth/me");
  set(fromMe(me));
}

async function bootstrap(): Promise<void> {
  if (bootstrapped || typeof window === "undefined") return;
  bootstrapped = true;
  setUnauthorizedHandler(() => set(ANON));
  if (!tokens.getRefresh()) {
    set(ANON);
    return;
  }
  try {
    if (await refreshSession()) await loadMe();
    else set(ANON);
  } catch {
    tokens.clear();
    set(ANON);
  }
}

export const authStore = {
  get: () => state,
  subscribe(l: () => void) {
    listeners.add(l);
    void bootstrap();
    return () => listeners.delete(l);
  },
  async login(email: string, password: string): Promise<void> {
    const pair = await apiFetch<TokenPair>("/auth/login", {
      method: "POST",
      body: { email, password },
      auth: false,
    });
    storeTokenPair(pair);
    await loadMe();
  },
  async signup(input: SignupInput): Promise<void> {
    const pair = await apiFetch<TokenPair>("/auth/signup", {
      method: "POST",
      body: input,
      auth: false,
    });
    storeTokenPair(pair);
    await loadMe();
  },
  async logout(): Promise<void> {
    const refresh = tokens.getRefresh();
    try {
      if (tokens.getAccess()) {
        await apiFetch<void>("/auth/logout", {
          method: "POST",
          body: { refreshToken: refresh },
          timeoutMs: 3000,
        });
      }
    } catch {
      /* 서버가 죽어 있어도 로컬 세션은 지운다 */
    } finally {
      tokens.clear();
      set(ANON);
    }
  },
  /** 계정 수정·비밀번호 변경 후 me 를 다시 읽는다. */
  refreshMe: loadMe,
  /** 테스트용 초기화 */
  _reset() {
    state = EMPTY;
    bootstrapped = false;
    tokens.clear();
  },
};

// ── mock 모드 ────────────────────────────────────────────────────────
function mockState(): AuthState {
  const u = mock.currentUser();
  if (!u) return ANON;
  const fac = u.facilityId
    ? (mock.getState().facilities.find((f) => f.id === u.facilityId) ?? null)
    : null;
  return {
    status: "authed",
    user: {
      id: u.id,
      email: u.email,
      name: u.name,
      service: u.service,
      role: u.role,
      facilityId: u.facilityId ?? null,
      onboarded: u.onboarded,
      createdAt: new Date(0).toISOString(),
    },
    facility: fac
      ? {
          id: fac.id,
          name: fac.name,
          inviteCode: fac.code,
          rootUserId: fac.rootUserId,
          createdAt: new Date(0).toISOString(),
        }
      : null,
    features: { fallSimulate: true },
  };
}

let mockHydrated = false;

export interface UseAuth extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  signup: (input: SignupInput) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
}

function useMockAuth(): UseAuth {
  const session = mock.useStore((st) => st.session);
  const users = mock.useStore((st) => st.users);
  const facilities = mock.useStore((st) => st.facilities);
  const hydrated = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => mockHydrated,
    () => false,
  );
  useEffect(() => {
    mock.hydrateSession();
    mockHydrated = true;
    for (const l of listeners) l();
  }, []);
  void session;
  void users;
  void facilities;
  const s: AuthState = !hydrated ? EMPTY : mockState();
  const login = useCallback(async (email: string, password: string) => {
    const r = mock.login(email, password);
    if (!r.ok) throw new ApiError(401, "INVALID_CREDENTIALS", r.error ?? "로그인 실패");
  }, []);
  const signup = useCallback(async (input: SignupInput) => {
    const r = mock.signup({
      email: input.email,
      password: input.password,
      name: input.name,
      service: input.service,
      facilityMode: input.facilityMode ?? undefined,
      facilityName: input.facilityName ?? undefined,
      inviteCode: input.inviteCode ?? undefined,
    });
    if (!r.ok) throw new ApiError(400, "SIGNUP_FAILED", r.error ?? "가입 실패");
  }, []);
  const logout = useCallback(async () => {
    mock.logout();
  }, []);
  const refreshMe = useCallback(async () => {}, []);
  return { ...s, login, signup, logout, refreshMe };
}

function useRealAuth(): UseAuth {
  const s = useSyncExternalStore(authStore.subscribe, authStore.get, () => EMPTY);
  return {
    ...s,
    login: authStore.login,
    signup: authStore.signup,
    logout: authStore.logout,
    refreshMe: authStore.refreshMe,
  };
}

/** 빌드 상수로 한 번만 선택 — 훅 호출 순서가 바뀌지 않는다. */
export const useAuth: () => UseAuth =
  import.meta.env.VITE_USE_MOCK === "1" ? useMockAuth : useRealAuth;
