/**
 * 도메인 타입 — 서버 OpenAPI 코드젠(src/api/generated/openapi.ts)의 재수출 + UI 상수.
 *
 * 타입의 진실원은 백엔드 `packages/contracts/api.py` 다. `bun run api:types` 로 갱신한다.
 * Resident 의 런타임 필드(state, mv, wander, presence, lastActivityAt, confidence, online)는
 * 실시간 경로가 붙기 전까지 항상 null 이며, null 은 "낙상 감지가 동작하지 않음"을 뜻한다 (낙상 없음이 아니다).
 */
import type { components } from "@/api/generated/openapi";

type S = components["schemas"];

// ── 계정 · 시설 ─────────────────────────────────────────────────────
export type UserAccount = S["UserOut"];
export type Me = S["MeOut"];
export type Facility = S["FacilityOut"];
export type FacilityMember = S["MemberOut"];
export type TokenPair = S["TokenPair"];
export type SignupInput = S["SignupIn"];
export type Service = UserAccount["service"];
export type Role = UserAccount["role"];

// ── 기기 ────────────────────────────────────────────────────────────
export type Device = S["DeviceOut"];
export type DeviceInput = S["DeviceIn"];
export type DevicePatch = S["DevicePatch"];
export type Connection = Device["connection"];
export type CalibrationStage = Device["calibrationStage"];

// ── 거주자 ──────────────────────────────────────────────────────────
/** deviceIds 는 서버가 항상 배열로 내려준다 (Pydantic default_factory 때문에 코드젠에서는 optional 로 나온다). */
export type Resident = Omit<S["ResidentOut"], "deviceIds"> & { deviceIds: string[] };
export type ResidentInput = S["ResidentIn"];
export type ResidentPatch = S["ResidentPatch"];
export type StateMachine = NonNullable<Resident["state"]>;
export type Presence = NonNullable<Resident["presence"]>;

// ── 낙상 이력 · 로그 · 수신자 · 설정 ────────────────────────────────
export type FallEvent = S["FallOut"];
export type FallResponse = FallEvent["response"];
export type FallPage = S["FallPage"];
export type EventLogEntry = S["EventLogOut"];
export type EventLogInput = S["EventLogIn"];
export type EventLogPage = S["EventLogPage"];
export type LogLevel = EventLogEntry["level"];
export type Recipient = S["RecipientOut"];
export type RecipientInput = S["RecipientIn"];
export type RecipientPatch = S["RecipientPatch"];
export type RecipientRole = Recipient["role"];
export type TenantConfig = S["TenantConfigOut"];
export type TenantConfigInput = S["TenantConfigIn"];
export type ApiErrorBody = S["ErrorOut"];

// ── 감지 상태 (F-W11 3상태) ─────────────────────────────────────────
/** ACTIVE = 실시간 경로 정상 · INACTIVE = 낙상 감지 미동작(엣지/추론 미연결) · OFFLINE = 백엔드 자체 미응답 */
export type DetectionStatus = "ACTIVE" | "INACTIVE" | "OFFLINE";

// ── UI 상수 ─────────────────────────────────────────────────────────
export const RECIPIENT_ROLE_LABEL: Record<RecipientRole, string> = {
  FAMILY: "가족",
  CAREGIVER: "요양사",
  ADMIN: "관리자",
};
export const RECIPIENT_ROLES: RecipientRole[] = ["FAMILY", "CAREGIVER", "ADMIN"];

export const FALL_RESPONSE_LABEL: Record<FallResponse, string> = {
  PENDING: "대기중",
  ACKNOWLEDGED: "확인함",
  DISPATCHED: "출동중",
  FALSE_ALARM: "오탐지",
};
export const FALL_RESPONSES: FallResponse[] = [
  "PENDING",
  "ACKNOWLEDGED",
  "DISPATCHED",
  "FALSE_ALARM",
];

/** 4단계 61초 캘리브레이션 (backend onboarding.run_calibration 타이밍과 동일). */
export const CALIBRATION_PHASE_SECONDS: Record<CalibrationStage, number> = {
  IDLE: 0,
  LEAVING: 30,
  WAITING_ACK: 0.2,
  WAITING_AGC: 1,
  MEASURING: 30,
  DONE: 0,
  ERROR: 0,
};
export const CALIBRATION_STAGE_ORDER: CalibrationStage[] = [
  "LEAVING",
  "WAITING_ACK",
  "WAITING_AGC",
  "MEASURING",
];
export const CALIBRATION_STAGE_LABEL: Record<CalibrationStage, string> = {
  IDLE: "대기",
  LEAVING: "공간 비우는 중",
  WAITING_ACK: "장치 응답 대기 중",
  WAITING_AGC: "AGC 보정 중",
  MEASURING: "움직임/재실 baseline 측정 중",
  DONE: "완료",
  ERROR: "오류",
};

/** HOME 공간 목록 (기능정의서 F-034). '호' 접미어를 붙이지 않는다. */
export const HOME_SPACES = [
  "거실",
  "침실",
  "안방",
  "주방",
  "화장실",
  "현관",
  "복도",
  "기타",
] as const;
