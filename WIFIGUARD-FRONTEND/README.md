# WIFI-GUARD Frontend (웹 관제 대시보드)

TanStack Start(React 19) 기반 실시간 관제 대시보드. **화면은 이미 다 있다.** 인메모리 목업 스토어가 소유하던 상태를 클라우드 API/WebSocket으로 옮기는 것이 이 레포의 과제다.

```
현재                                    목표
────────────────────────────────────    ────────────────────────────────────
mock-store.ts (1,195줄)                  TanStack Query 훅
  · setInterval(tick, 100) 시뮬레이션      · REST + WS (다기기 구독)
  · FACILITY 100% 목업                     · FACILITY 실데이터
  · localStorage 세션                      · JWT
backend.ts → 127.0.0.1:8000 하드코딩      → 환경변수 (클라우드 API)
```

> ## 현재 상태 (2026-09-10)
>
> **실시간 재실이 화면까지 흐른다.** `src/api/realtime/`이 `/ws/live`에 붙어 기기별 재실
> 상태와 MV 시계열을 받는다. HOME·FACILITY 구분 없이 동작하며, 소켓은 앱 전역에서 하나다.
> `tsc` 0오류 · `eslint` 0오류 · **Vitest 34개** · `bun run build` 성공.
>
> **완료**: 12개 라우트 실 API + JWT 세션 복원 + 401 재시도 · 실시간 구독(지수 백오프,
> 토큰 만료 시 refresh 후 즉시 재연결, 깨진 프레임 방어) · 낙상 알람 경로 · 링크 진단 패널.
>
> **미완**: 낙상 축은 백엔드 추론(M5)이 붙어야 값이 온다 — 그때까지 `fall`은 항상 null이고
> 화면은 "낙상 감지 미동작"을 표시한다. **그것이 "이상 없음"이 아니라는 점이 이 화면의 계약이다.**
> 캘리브레이션 61초는 아직 `src/lib/calibration-sim.ts`의 클라이언트 시뮬레이션이다
> (엣지가 `calibrate` 명령을 아직 처리하지 않는다 — 등록된 것은 `ping`뿐).
>
> 무엇을 바꿔야 하는지는 [PORTING.md](PORTING.md)를 볼 것.

---

## 책임 경계

| 하는 것                                                                   | 하지 않는 것                                   |
| ------------------------------------------------------------------------- | ---------------------------------------------- |
| 실시간 관제 · 낙상 이력 · 이벤트 로그 · 장치/거주자 관리 · 알림 · 설정 UI | 신호처리·감지 판정 → Pi(재실) / 클라우드(낙상) |
| 클라우드 API 연동 (TanStack Query) + WebSocket 실시간 구독                | 알림 발송 → 백엔드 `notification` 서비스       |
| JWT 인증 · 역할 기반 화면 가드                                            | 기기 직접 통신 → 백엔드 → MQTT → Pi            |
| 낙상 전체화면 알람 (`/ws/live` 구독, **앱 내 경로**)                      |                                                |
| 캘리브레이션 진행 UI (4단계 61초)                                         |                                                |

> **알림 이중 경로**: 휴대폰 푸시(SMS/ntfy)는 백엔드에서 직접 나가고 프론트를 거치지 않는다. 앱 내부 `FallAlarmModal`은 `/ws/live`의 `detect_state`를 구독하는 **별개 경로**다. 이 이중화가 한 쪽 장애 시의 안전망이며, 프론트는 자신의 경로만 책임진다.

---

## 실행

```bash
bun install
cp .env.example .env      # VITE_API_BASE_URL 을 백엔드 주소로. 없으면 http://127.0.0.1:8000
bun run dev               # http://localhost:8080
```

백엔드는 로컬(`make api`) 또는 EC2(SSH 터널 `-L 8000:127.0.0.1:8000`) 중 하나여야 한다.
둘은 같은 8000 포트를 쓰므로 동시에 띄우지 않는다.

| 명령                          |                              |
| ----------------------------- | ---------------------------- |
| `bun run dev`                 | vite 개발 서버 (8080)        |
| `bun run build` / `build:dev` | 프로덕션 / 개발 빌드         |
| `bun run preview`             | 빌드 결과 미리보기           |
| `bun run typecheck`           | `tsc --noEmit`               |
| `bun run test`                | Vitest (13개)                |
| `bun run lint` / `format`     | eslint / prettier            |
| `bun run api:types`           | 백엔드 openapi.json → 타입 생성 |

Playwright(E2E)는 아직 없다 — 도입은 별도 합의 사항.

## 구조

```
src/
  routes/            파일기반 라우팅 13개 — 전부 실 API   routeTree.gen.ts 는 자동생성 — 손대지 말 것
  components/        AuthGate · AppSidebar · FallAlarmModal · EventLogPanel
    LiveBridge.tsx       소켓을 앱 전역에서 유지 + 낙상 전이 → 알람
    LinkStatusPanel.tsx  기기 링크 진단 (수신률·RSSI·체크섬 오류)
  components/ui/     shadcn/radix 프리미티브 50개
  api/               ★ 데이터 계층                                       [동작]
    client.ts        apiFetch · /api/v1 자동 접두 · 401 재시도
    auth.ts          authStore · useAuth
    queries/ mutations/   조회 10종 · 변경 17종 (빌드 상수로 실API/목업 선택)
    mock/            VITE_USE_MOCK=1 에서만 번들에 남는다
    generated/       openapi.json → 타입 (수정 금지, `bun run api:types`)
    realtime/        ★ 실시간                                            [동작]
      types.ts         프레임 타입 + **런타임 파서**(신뢰할 수 없는 입력 방어)
      socket.ts        연결 하나 · 지수 백오프 · 토큰 만료 시 즉시 재연결
      useLiveStream.ts useLive · useLiveDevice · useMvSeries · useDetectionState
  lib/
    domain.ts format.ts   도메인 타입 · 라벨("감지 미동작")
    mock-store.ts    목업 시뮬레이션 (1,140줄)      [VITE_USE_MOCK=1 전용]
    calibration-sim.ts   61.2초 데모 타이머 — 결과값이 난수다 (엣지 cmd 미구현)
    error-*.ts       SSR 오류 처리 — 건드리지 말 것
docs/                명세 문서
.env.example         VITE_API_BASE_URL · VITE_WS_URL · VITE_USE_MOCK
```

## 실시간 계약

`/ws/live` 프레임은 **snake_case** 다. REST(`generated/openapi.ts`)가 camelCase 인 것과 다르며,
재실 필드 이름이 엣지 dataclass → MQTT → DB 컬럼 → WS 까지 한 번도 바뀌지 않게 하려는 것이다.

타입의 진실원은 백엔드가 생성하는 `packages/contracts/realtime.schema.json`
(= `GET /realtime/schema`)이고, `src/api/__tests__/realtime-contract.test.ts` 가 그 파일과
직접 대조해 드리프트를 잡는다. 구 `src/lib/backend.ts` 의 `LiveSample` 은 손으로 미러링한
탓에 서버 계약과 조용히 갈라져 있었다.

```
클라이언트 → 서버   auth(첫 프레임, 5초 안) → subscribe / unsubscribe / ping
서버 → 클라이언트   hello → live* → pong / error
```

**`presence`/`fall` 이 null 이면 그 축이 동작하지 않는 것이다.** "이상 없음"이 아니다.
`Device.online` 도 3상태다 — null 은 "모름"(텔레메트리를 받은 적 없음), false 는 "연결 끊김".

---

## 세 축의 임계값 — 절대 섞지 말 것

| 값                                | 출처              | 척도                                 | UI 라벨                                |
| --------------------------------- | ----------------- | ------------------------------------ | -------------------------------------- |
| `presence_mv_threshold`           | 캘리브레이션 (Pi) | MV 스케일, 기본 2.0                  | **"움직임 임계값"**                    |
| `wander_baseline`                 | 캘리브레이션 (Pi) | Welch PSD 스케일, 기본 0.5           | **"재실 baseline"**                    |
| `wander_ratio_threshold`          | 설정 (Pi)         | **baseline 대비 배수** 1~5, 기본 1.8 | "WANDER 비율 임계값"                   |
| `threshold` = **0.468**           | 모델 (클라우드)   | **확률** 0~1                         | **"판정 임계값"** / "낙상 확률 임계값" |
| `PipelineConfig.wander_threshold` | 목업 전용         | **절대값** 0~1                       | (목업 폴백에서만)                      |

목업의 `wander_threshold`(절대값)와 실백엔드의 `wander_ratio_threshold`(배수)는 **이름이 비슷해도 서로 변환되지 않는 별개의 숫자**다.

## `/ws/live` 계약 — 낙상 필드의 부재는 "낙상 없음"이 아니다

재실 필드(`presence_state`, `mv_current` …)는 엣지가 연결되어 있으면 **항상** 있고, 낙상 필드(`detect_state`, `proba_fall` …)는 모델이 로드된 경우에만 추가된다. **낙상 필드가 없으면 "낙상 감지가 동작하지 않음"이다.** 안전 기능이므로 이 상태를 무증상으로 넘기면 안 된다 — 사이드바 3상태 표시(정상 / **낙상 감지 중단** / 완전 오프라인) + 대시보드 배너가 Phase 4-2의 신규 요구다.

## 도메인 용어

| 개념                 | UI 표기                                                              |
| -------------------- | -------------------------------------------------------------------- |
| MV / moving variance | **움직임 감지** (임계값·라벨), "이동 분산 (딥러닝 입력)" (신호 자체) |
| 낙상 상태            | IDLE=**대기**, SUSPECT=**의심**, FALL=**낙상**, COOLDOWN=**냉각중**  |
| 재실 상태            | PRESENT=**재실**, ABSENT=**퇴실**                                    |
| 서비스 유형          | HOME=**가정**, FACILITY=**시설**                                     |
| FACILITY 역할        | ROOT=**시설 등록자**, MEMBER=**초대코드로 참여**                     |
| 응답 상태            | 대기중 / 확인함 / 출동중 / 오탐지                                    |

---

## 문서

- [PORTING.md](PORTING.md) — 이식 매핑 · 전환 작업 목록 · 미결정 사항
- [CLAUDE.md](CLAUDE.md) — 아키텍처 · 컨벤션 (프론트 전용판)
- [docs/WIFI-GUARD\_레포명세\_frontend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_frontend_v1.0_20260804.md) — 이 레포의 명세
- [docs/WIFI-GUARD\_레포명세\_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) §7 — **4레포 공통 계약 SSOT**
- [docs/CSI-Guard\_기능정의서\_HOME_v1.0_20260717.md](docs/CSI-Guard_기능정의서_HOME_v1.0_20260717.md) — F-001~F-077 기능 정의
- [docs/CSI-Guard\_기능명세서\_v2.0_20260715.md](docs/CSI-Guard_기능명세서_v2.0_20260715.md) — 페이지별 상세 (현행 구현 기준)
- [docs/CSI-Guard*HOME*유저플로우\_v1.0_20260715.md](docs/CSI-Guard_HOME_유저플로우_v1.0_20260715.md) — 8개 사용자 시나리오
