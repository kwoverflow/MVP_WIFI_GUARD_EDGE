/**
 * `/ws/live` 연결 관리 — 앱 전체에서 소켓 하나.
 *
 * 구 `src/lib/backend.ts:194-265` 도 싱글턴이었지만 세 가지가 달랐다.
 *   1. **기기 개념이 없었다.** 전역 샘플 하나뿐이라 FACILITY 다기기를 표현할 수 없었다.
 *   2. **고정 2초 재연결.** 서버가 죽어 있으면 2초마다 무한히 두드렸다.
 *   3. **`JSON.parse` 에 try/catch 가 없었다.** 깨진 프레임 하나로 스트림이 끊겼다.
 *
 * 소켓을 하나로 두는 이유: 서버가 이미 테넌트 단위로 팬아웃하므로 연결을 늘려도 얻는 게
 * 없고, 구독자마다 연결하면 인증·백오프 상태가 흩어진다.
 */

import { API_BASE, refreshSession, tokens } from "@/api/client";

import {
  PROTOCOL_VERSION,
  WS_CLOSE_TOKEN_EXPIRED,
  parseServerEvent,
  type ClientFrame,
  type DeviceLive,
  type ServerEvent,
} from "./types";

export type LiveStatus = "idle" | "connecting" | "open" | "reconnecting" | "unauthorized";

/** MV 차트용 시계열. 기기별로 따로 쌓는다. */
export interface MvPoint {
  t: number;
  mv: number;
}

export interface LiveSnapshot {
  status: LiveStatus;
  /** 기기별 최신 상태. 서버가 보낸 것만 들어 있다 — 없는 기기는 "감지 미동작". */
  devices: Readonly<Record<string, DeviceLive>>;
  /** 기기별 MV 시계열 (최근 `HISTORY_MAX` 개). */
  history: Readonly<Record<string, MvPoint[]>>;
  /** 이 사용자 스코프의 전체 기기 (hello 가 알려 준다). */
  deviceIds: readonly string[];
  lastError: string | null;
  /** 진단용 — 파싱에 실패해 버린 프레임 수. */
  droppedFrames: number;
}

// Pi5 bundles use an empty API base: the same build works from any LAN address.
function websocketUrl(apiBase: string, override: string | undefined, origin: string): string {
  return override || `${(apiBase || origin).replace(/\/$/, "").replace(/^http/, "ws")}/ws/live`;
}
const WS_URL = websocketUrl(
  API_BASE,
  import.meta.env.VITE_WS_URL,
  typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8000",
);

/** 10Hz 기준 30초. 구 구현과 같은 길이지만 **기기별**이다. */
const HISTORY_MAX = 300;
const BACKOFF_MIN_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
/** 서버가 살아 있는지 확인하는 주기. 유휴 연결이 프록시에 끊기는 것도 막는다. */
const PING_INTERVAL_MS = 25_000;

const EMPTY: LiveSnapshot = {
  status: "idle",
  devices: {},
  history: {},
  deviceIds: [],
  lastError: null,
  droppedFrames: 0,
};

let snapshot: LiveSnapshot = EMPTY;
let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let pingTimer: ReturnType<typeof setInterval> | null = null;
let attempt = 0;
let refreshedOnce = false;
let wanted = false; // 구독자가 있는가

const listeners = new Set<() => void>();

function emit(next: Partial<LiveSnapshot>) {
  snapshot = { ...snapshot, ...next };
  for (const l of listeners) l();
}

function send(frame: ClientFrame) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(frame));
}

/** 지수 백오프 + 지터. 여러 탭이 동시에 재연결해 서버를 때리는 것을 막는다. */
function backoffMs(): number {
  const base = Math.min(BACKOFF_MIN_MS * 2 ** attempt, BACKOFF_MAX_MS);
  return base + Math.random() * Math.min(base, 1_000);
}

function clearTimers() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
}

/**
 * 재연결 예약. **증가는 여기서만 한다** — 호출자가 각자 올리면 첫 재시도가 1초가 아니라
 * 2초부터 시작하는 식으로 어긋난다(실제로 그랬다).
 */
function scheduleReconnect(immediate = false) {
  if (!wanted || reconnectTimer) return;
  const delay = immediate ? 0 : backoffMs();
  if (!immediate) attempt += 1;
  emit({ status: "reconnecting" });
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function onEvent(event: ServerEvent) {
  switch (event.type) {
    case "hello": {
      attempt = 0; // 성공했으니 백오프를 되감는다
      refreshedOnce = false;
      if (event.protocol_version !== PROTOCOL_VERSION) {
        // 계약이 갈라졌다. 끊지는 않되 눈에 보이게 남긴다 — 조용히 다른 형태를
        // 읽으면 화면이 이유 없이 비는 것으로만 드러난다.
        emit({
          lastError: `프로토콜 버전 불일치: 서버 ${event.protocol_version} / 클라이언트 ${PROTOCOL_VERSION}`,
        });
      }
      emit({ status: "open", deviceIds: event.device_ids });
      send({ type: "subscribe", device_ids: [] }); // 빈 목록 = 스코프 전체
      break;
    }
    case "live": {
      const devices = { ...snapshot.devices };
      const history = { ...snapshot.history };
      for (const d of event.devices) {
        devices[d.device_id] = d;
        const mv = d.presence?.mv_current;
        if (mv != null) {
          const prev = history[d.device_id] ?? [];
          const next =
            prev.length >= HISTORY_MAX ? prev.slice(prev.length - HISTORY_MAX + 1) : prev.slice();
          next.push({
            t: Date.parse(d.presence?.updated_at ?? event.server_time) || Date.now(),
            mv,
          });
          history[d.device_id] = next;
        }
      }
      emit({ devices, history });
      break;
    }
    case "error":
      emit({ lastError: `${event.code}: ${event.detail}` });
      break;
    case "pong":
      break;
  }
}

function connect() {
  if (!wanted || typeof window === "undefined") return;
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
  )
    return;

  const token = tokens.getAccess();
  if (!token) {
    // 아직 로그인 전이거나 복원 중이다. 조금 뒤 다시 본다.
    emit({ status: "reconnecting" });
    scheduleReconnect();
    return;
  }

  emit({ status: attempt === 0 ? "connecting" : "reconnecting" });
  let ws: WebSocket;
  try {
    ws = new WebSocket(WS_URL);
  } catch (err) {
    emit({ lastError: String(err) });
    scheduleReconnect();
    return;
  }
  socket = ws;

  ws.onopen = () => {
    send({ type: "auth", token });
    pingTimer = setInterval(() => send({ type: "ping" }), PING_INTERVAL_MS);
  };

  ws.onmessage = (ev) => {
    if (typeof ev.data !== "string") return;
    const parsed = parseServerEvent(ev.data);
    if (parsed === null) {
      emit({ droppedFrames: snapshot.droppedFrames + 1 });
      return;
    }
    onEvent(parsed);
  };

  ws.onerror = () => {
    // onclose 가 이어서 오므로 여기서는 상태만 남긴다.
    emit({ lastError: "WebSocket 오류" });
  };

  ws.onclose = (ev) => {
    socket = null;
    clearTimers();
    if (!wanted) {
      emit({ status: "idle" });
      return;
    }
    // access 만료는 refresh 후 **즉시** 한 번 다시 시도한다. 백오프를 태우면
    // 15분마다 화면이 몇 초씩 비게 된다.
    if (ev.code === WS_CLOSE_TOKEN_EXPIRED && !refreshedOnce) {
      refreshedOnce = true;
      void refreshSession().then((ok) => {
        if (ok) scheduleReconnect(true);
        else emit({ status: "unauthorized", lastError: "세션이 만료되었습니다." });
      });
      return;
    }
    scheduleReconnect();
  };
}

export const liveSocket = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    if (listeners.size === 1) {
      wanted = true;
      attempt = 0;
      connect();
    }
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) {
        // 마지막 구독자가 사라지면 연결을 닫는다. 로그아웃·페이지 이탈 시 소켓이
        // 남아 토큰을 붙든 채 재연결을 반복하는 것을 막는다.
        wanted = false;
        clearTimers();
        socket?.close();
        socket = null;
        snapshot = EMPTY;
      }
    };
  },
  getSnapshot: (): LiveSnapshot => snapshot,
  /** SSR — 서버에서는 항상 빈 스냅샷 (hydration 불일치 방지). */
  getServerSnapshot: (): LiveSnapshot => EMPTY,

  /** 로그아웃 등으로 토큰이 바뀌었을 때 강제 재연결. */
  reset() {
    attempt = 0;
    refreshedOnce = false;
    clearTimers();
    socket?.close();
    socket = null;
    snapshot = { ...EMPTY, status: wanted ? "connecting" : "idle" };
    for (const l of listeners) l();
    if (wanted) connect();
  },

  /** 테스트 전용 — 내부 상태를 초기화한다. */
  __resetForTests() {
    wanted = false;
    attempt = 0;
    refreshedOnce = false;
    clearTimers();
    socket?.close();
    socket = null;
    snapshot = EMPTY;
    listeners.clear();
  },
};

export const __testing = {
  WS_URL,
  HISTORY_MAX,
  BACKOFF_MIN_MS,
  BACKOFF_MAX_MS,
  backoffMs,
  websocketUrl,
};
