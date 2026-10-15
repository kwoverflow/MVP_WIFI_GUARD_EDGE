/**
 * 프론트 타입 ↔ 백엔드 `realtime.schema.json` 드리프트 검사.
 *
 * 구 `src/lib/backend.ts` 의 `LiveSample` 은 손으로 미러링한 탓에 서버 계약과 갈라져 있었다
 * (명세는 재실 필드를 필수로 규정했는데 구현은 전부 optional). 런타임에 드러나지 않는
 * 종류의 결함이라, 스키마 파일과 직접 대조하지 않으면 다음 사람이 또 깨뜨린다.
 *
 * 스키마 파일은 백엔드가 `make openapi` 로 생성한다. 없으면 이 테스트는 건너뛴다 —
 * 프론트만 체크아웃한 환경에서 실패하면 안 되기 때문이다.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CLIENT_FRAME_TYPES,
  DEVICE_LIVE_FIELDS,
  FALL_FIELDS,
  PRESENCE_FIELDS,
  PROTOCOL_VERSION,
  SERVER_EVENT_TYPES,
  parseServerEvent,
} from "@/api/realtime/types";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCHEMA_PATH = resolve(
  HERE,
  "../../../../WIFIGUARD-BACKEND/packages/contracts/realtime.schema.json",
);

type Schema = {
  "x-protocol-version": number;
  $defs: Record<string, { properties?: Record<string, unknown>; required?: string[] }>;
};

const schema: Schema | null = existsSync(SCHEMA_PATH)
  ? (JSON.parse(readFileSync(SCHEMA_PATH, "utf8")) as Schema)
  : null;

const withSchema = schema ? describe : describe.skip;

withSchema("realtime.schema.json 대조", () => {
  const defs = schema!.$defs;

  it("프로토콜 버전이 같다", () => {
    expect(PROTOCOL_VERSION).toBe(schema!["x-protocol-version"]);
  });

  it.each([
    ["PresenceBlock", PRESENCE_FIELDS],
    ["FallBlock", FALL_FIELDS],
    ["DeviceLive", DEVICE_LIVE_FIELDS],
  ] as const)("%s 의 필드 집합이 스키마와 정확히 같다", (name, fields) => {
    expect(defs[name]).toBeDefined();
    expect(new Set(Object.keys(defs[name].properties ?? {}))).toEqual(new Set(fields));
  });

  it("서버 이벤트·클라이언트 프레임 종류가 스키마에 모두 있다", () => {
    const present = new Set(Object.keys(defs));
    for (const t of SERVER_EVENT_TYPES) {
      const cls = `${t[0].toUpperCase()}${t.slice(1)}Event`;
      expect(present.has(cls), `${cls} 가 스키마에 없다`).toBe(true);
    }
    for (const t of CLIENT_FRAME_TYPES) {
      if (t === "auth") continue; // AuthFrame
      const cls = `${t[0].toUpperCase()}${t.slice(1)}Frame`;
      expect(present.has(cls), `${cls} 가 스키마에 없다`).toBe(true);
    }
    expect(present.has("AuthFrame")).toBe(true);
  });

  it("재실 필드가 snake_case 를 유지한다 (엣지·DB 와 같은 이름)", () => {
    for (const f of PRESENCE_FIELDS) {
      expect(f).not.toMatch(/[A-Z]/);
      expect(f.startsWith("presence_")).toBe(false); // 접두는 폐기됐다
    }
  });
});

describe("parseServerEvent — 신뢰할 수 없는 입력 방어", () => {
  it("깨진 JSON 은 예외 대신 null", () => {
    expect(parseServerEvent("not json")).toBeNull();
    expect(parseServerEvent("")).toBeNull();
    expect(parseServerEvent("[1,2,3]")).toBeNull();
  });

  it("type 이 없거나 모르는 값이면 null", () => {
    expect(parseServerEvent('{"foo":1}')).toBeNull();
    expect(parseServerEvent('{"type":"quantum"}')).toBeNull();
  });

  it("hello 를 파싱한다", () => {
    const ev = parseServerEvent(
      JSON.stringify({
        type: "hello",
        protocol_version: 1,
        tenant_id: "home-1",
        user_id: "u1",
        device_ids: ["d1", "d2", 42],
        server_time: "2026-09-10T00:00:00Z",
      }),
    );
    expect(ev).toMatchObject({ type: "hello", tenant_id: "home-1" });
    if (ev?.type !== "hello") throw new Error("hello 가 아니다");
    expect(ev.device_ids).toEqual(["d1", "d2"]);
  });

  it("presence.state 가 없으면 블록을 버린다", () => {
    // 반쪽짜리 재실 정보는 "감지 미동작"과 구별할 수 없어 오히려 위험하다.
    const ev = parseServerEvent(
      JSON.stringify({
        type: "live",
        devices: [{ device_id: "d1", presence: { mv_current: 3.1 } }],
        server_time: "t",
      }),
    );
    if (ev?.type !== "live") throw new Error("live 가 아니다");
    expect(ev.devices[0].presence).toBeNull();
  });

  it("device_id 가 없는 항목은 버리고 나머지는 살린다", () => {
    const ev = parseServerEvent(
      JSON.stringify({
        type: "live",
        devices: [{ presence: { state: "present" } }, { device_id: "d2" }],
        server_time: "t",
      }),
    );
    if (ev?.type !== "live") throw new Error("live 가 아니다");
    expect(ev.devices.map((d) => d.device_id)).toEqual(["d2"]);
  });

  it("online 3상태를 보존한다", () => {
    const parse = (online: unknown) =>
      (
        parseServerEvent(
          JSON.stringify({ type: "live", devices: [{ device_id: "d", online }], server_time: "t" }),
        ) as { devices: { online: boolean | null }[] }
      ).devices[0].online;

    expect(parse(true)).toBe(true);
    expect(parse(false)).toBe(false);
    expect(parse(undefined)).toBeNull(); // 모름 — "연결 끊김"과 다르다
    expect(parse("yes")).toBeNull();
  });
});
