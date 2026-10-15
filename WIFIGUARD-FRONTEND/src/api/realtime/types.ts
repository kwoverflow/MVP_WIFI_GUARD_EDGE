/**
 * `/ws/live` 프레임 타입 + **런타임 파서**.
 *
 * 계약의 진실원은 백엔드 `packages/contracts/realtime.py` 이고,
 * 그 JSON Schema 가 `packages/contracts/realtime.schema.json` (= `GET /realtime/schema`) 이다.
 * `__tests__/realtime-contract.test.ts` 가 아래 필드 목록을 그 스키마와 대조해 드리프트를 잡는다.
 * 구 `src/lib/backend.ts` 의 `LiveSample` 은 손으로 미러링한 탓에 서버 계약과 조용히 갈라져
 * 있었다(명세는 재실 필드를 필수로 규정했는데 구현은 전부 optional 이었다).
 *
 * ## 왜 런타임 파서까지 두는가
 *
 * WebSocket 으로 들어오는 것은 **신뢰할 수 없는 입력**이다. 구 구현은 `onmessage` 에서
 * `JSON.parse` 를 try/catch 없이 불러, 깨진 프레임 하나가 예외를 던지고 스트림을 끊었다.
 * 타입 단언(`as ServerEvent`)만으로는 그 문제가 남는다.
 *
 * ## 필드가 snake_case 인 이유
 *
 * REST(`src/api/generated/openapi.ts`)는 camelCase 지만 WS 는 snake_case 다. 재실 필드 이름이
 * 엣지 dataclass → MQTT → DB 컬럼 → WS 까지 **한 번도 바뀌지 않게** 하려는 것이다.
 * 중간에 이름을 바꾸던 지점이 실제로 있었고, 그 탓에 DB 컬럼 하나가 영원히 비어 있었다.
 */

export const PROTOCOL_VERSION = 1;

/** 서버가 인증 실패·미인증으로 닫을 때. RFC 6455 policy violation. */
export const WS_CLOSE_UNAUTHORIZED = 1008;
/** access 토큰 만료. refresh 후 즉시 1회 재연결한다(백오프 없이). */
export const WS_CLOSE_TOKEN_EXPIRED = 4001;

export type PresenceState = "present" | "absent";
export type FallState = "IDLE" | "SUSPECT" | "FALL" | "COOLDOWN";
export type PostProcess = "none" | "causal_mode5" | "centered_mode5" | "segmentation_a" | "segmentation_b";

/** `PresenceMsg` 의 상태 11필드. 이름이 엣지·DB 와 같다. */
export interface PresenceBlock {
  state: PresenceState;
  mv_current: number | null;
  wander_current: number | null;
  mv_threshold: number | null;
  wander_baseline: number | null;
  wander_ratio_threshold: number | null;
  wander_ratio: number | null;
  wander_confirmed: boolean | null;
  last_activity_at: number | null;
  seconds_since_activity: number | null;
  just_changed: boolean | null;
  updated_at: string;
}

export interface FallBlock {
  detect_state: FallState;
  proba_fall: number | null;
  threshold: number;
  postprocess: PostProcess;
  fall_count: number;
  last_fall_time: number | null;
  fall_event_id?: string | null;
  updated_at: string;
}

export interface LinkStats {
  connected: boolean;
  transport: "uart" | "spi" | "replay";
  port: string | null;
  baud: number | null;
  reconnects: number;
  frames_ok: number;
  checksum_errors: number;
  resyncs: number;
  mac_filtered: number;
  hz_1s: number | null;
  rssi: number | null;
  buffered_seconds: number | null;
  amp_mean: number | null;
  amp_std: number | null;
}

/**
 * 한 기기의 현재 상태.
 *
 * `presence`/`fall` 이 `null` 이면 **그 축이 동작하지 않는 것**이다. "이상 없음"이 아니다.
 * `online` 도 3상태다 — `null` 은 "모름"(텔레메트리를 받은 적 없음), `false` 는 "연결 끊김".
 */
export interface DeviceLive {
  device_id: string;
  online: boolean | null;
  last_seen_at: string | null;
  link: LinkStats | null;
  presence: PresenceBlock | null;
  fall: FallBlock | null;
}

export interface HelloEvent {
  type: "hello";
  protocol_version: number;
  tenant_id: string;
  user_id: string;
  device_ids: string[];
  server_time: string;
}

export interface LiveEvent {
  type: "live";
  devices: DeviceLive[];
  server_time: string;
}

export interface ErrorEvent {
  type: "error";
  code: string;
  detail: string;
}

export interface PongEvent {
  type: "pong";
  server_time: string;
}

export type ServerEvent = HelloEvent | LiveEvent | ErrorEvent | PongEvent;

export type ClientFrame =
  | { type: "auth"; token: string }
  | { type: "subscribe"; device_ids: string[] }
  | { type: "unsubscribe"; device_ids: string[] }
  | { type: "ping" };

// ── 드리프트 테스트가 스키마와 대조하는 필드 목록 ─────────────────────
export const PRESENCE_FIELDS = [
  "state",
  "mv_current",
  "wander_current",
  "mv_threshold",
  "wander_baseline",
  "wander_ratio_threshold",
  "wander_ratio",
  "wander_confirmed",
  "last_activity_at",
  "seconds_since_activity",
  "just_changed",
  "updated_at",
] as const;

export const FALL_FIELDS = [
  "detect_state",
  "proba_fall",
  "threshold",
  "postprocess",
  "fall_count",
  "last_fall_time",
  "fall_event_id",
  "updated_at",
] as const;

export const DEVICE_LIVE_FIELDS = [
  "device_id",
  "online",
  "last_seen_at",
  "link",
  "presence",
  "fall",
] as const;

export const SERVER_EVENT_TYPES = ["hello", "live", "error", "pong"] as const;
export const CLIENT_FRAME_TYPES = ["auth", "subscribe", "unsubscribe", "ping"] as const;

// ── 런타임 파서 ──────────────────────────────────────────────────────
function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * 원문 → `ServerEvent`. 형태가 어긋나면 **null**(예외를 던지지 않는다).
 *
 * 깨진 프레임 하나가 스트림 전체를 끊으면 안 된다. 호출자는 null 을 세고 넘어간다.
 */
export function parseServerEvent(raw: string): ServerEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data) || typeof data.type !== "string") return null;

  switch (data.type) {
    case "hello":
      if (typeof data.tenant_id !== "string" || !Array.isArray(data.device_ids)) return null;
      return {
        type: "hello",
        protocol_version: typeof data.protocol_version === "number" ? data.protocol_version : 0,
        tenant_id: data.tenant_id,
        user_id: String(data.user_id ?? ""),
        device_ids: data.device_ids.filter((d): d is string => typeof d === "string"),
        server_time: String(data.server_time ?? ""),
      };
    case "live": {
      if (!Array.isArray(data.devices)) return null;
      const devices = data.devices
        .filter(isRecord)
        .map(toDeviceLive)
        .filter((d): d is DeviceLive => d !== null);
      return { type: "live", devices, server_time: String(data.server_time ?? "") };
    }
    case "error":
      return {
        type: "error",
        code: String(data.code ?? "UNKNOWN"),
        detail: String(data.detail ?? ""),
      };
    case "pong":
      return { type: "pong", server_time: String(data.server_time ?? "") };
    default:
      // 서버가 프로토콜을 늘렸을 수 있다. 모르는 type 은 조용히 버린다.
      return null;
  }
}

function toDeviceLive(d: Record<string, unknown>): DeviceLive | null {
  if (typeof d.device_id !== "string") return null;
  return {
    device_id: d.device_id,
    online: typeof d.online === "boolean" ? d.online : null,
    last_seen_at: typeof d.last_seen_at === "string" ? d.last_seen_at : null,
    link: isRecord(d.link) ? (d.link as unknown as LinkStats) : null,
    // ★ presence.state 가 없으면 블록 전체를 버린다 — 반쪽짜리 재실 정보는
    //   "감지 미동작"과 구별할 수 없어 오히려 위험하다.
    presence:
      isRecord(d.presence) && (d.presence.state === "present" || d.presence.state === "absent")
        ? (d.presence as unknown as PresenceBlock)
        : null,
    fall:
      isRecord(d.fall) && typeof d.fall.detect_state === "string"
        ? (d.fall as unknown as FallBlock)
        : null,
  };
}
