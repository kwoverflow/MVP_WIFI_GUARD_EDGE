# PORTING — WIFIGUARD-FRONTEND

이 레포는 원본 프론트엔드의 **무변경 사본**이다. `bun install && bun run dev`로 그대로 뜬다. 아직 어떤 전환도 하지 않았다.

- 명세: [docs/WIFI-GUARD\_레포명세\_frontend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_frontend_v1.0_20260804.md)
- 계약 SSOT: [docs/WIFI-GUARD\_레포명세\_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) §7
- 원본: `GAA/` = `csi_fall/MVP-MOCKUP/Guardian Angel Alert/`
- 배치 작업일: 2026-09-03

---

## 1. 이식 매핑

### 1-1. 무변경 복사

| 원본                                                                                                                     | 목표        | 비고                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------ | ----------- | ------------------------------------------------------------------------------------------ |
| `GAA/src/` 전체 (77 파일, 6,399줄 + ui 4,360줄)                                                                          | `src/`      | **바이트 동일.** `diff -rq`로 확인. `src/api/` 빈 디렉토리 4개만 추가                      |
| `GAA/public/`                                                                                                            | `public/`   | favicon.ico                                                                                |
| `GAA/package.json`, `bun.lock`                                                                                           | 동일        | 의존성 승계. React 19.2 · TanStack Router 1.170 / Start 1.168 / Query 5.101 · Tailwind 4.2 |
| `GAA/vite.config.ts`                                                                                                     | 동일        | `@lovable.dev/vite-tanstack-config` 래핑 유지                                              |
| `GAA/tsconfig.json`, `components.json`, `bunfig.toml`, `eslint.config.js`, `.prettierrc`, `.prettierignore`, `AGENTS.md` | 동일        | 무변경                                                                                     |
| `GAA/.lovable/` (plan.md, project.json)                                                                                  | `.lovable/` | **결정 대기** — §4-1                                                                       |

### 1-2. 신규 작성

| 파일                                              | 내용                                                                                                                            |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `.env.example`                                    | `VITE_API_BASE_URL` · `VITE_WS_URL` · `VITE_USE_MOCK` — **아직 읽는 코드가 없다**                                               |
| `.gitignore`                                      | 원본에서 `backend/`·`reference/`·`Window3BestModelInference/`·`esp32c5/`·`migration/` 등 프론트와 무관한 항목 제거, `.env` 추가 |
| `CLAUDE.md`                                       | 원본에서 백엔드·참조구현·로컬 파이썬 절을 걷어낸 프론트 전용판                                                                  |
| `README.md`, `PORTING.md`                         | 이 문서                                                                                                                         |
| `src/api/{generated,queries,mutations,realtime}/` | 빈 디렉토리. 명세 §3의 신규 데이터 계층 자리                                                                                    |

### 1-3. 복사하지 않은 것

| 원본                                                        | 이유                                              |
| ----------------------------------------------------------- | ------------------------------------------------- |
| `GAA/backend/`                                              | → WIFIGUARD-BACKEND / WIFIGUARD-RASPBERRY         |
| `GAA/docs/` 전체                                            | 프론트에 필요한 7종만 `docs/`로 (§1-4)            |
| `GAA/reference/`, `Window3BestModelInference/`              | 파이썬 연구 자산                                  |
| `GAA/node_modules/`, `.output/`, `.tanstack/`, `.wrangler/` | 빌드·설치 산출물. `bun install`로 재생성          |
| `GAA/CLAUDE.md`                                             | 프론트 전용판으로 재작성 (§1-2)                   |
| `GAA/README.md`                                             | 원본은 monorepo 전체 안내. 프론트 전용으로 재작성 |

### 1-4. `docs/`에 가져온 것

`WIFI-GUARD_레포명세_frontend` · `WIFI-GUARD_레포명세_backend`(§7 SSOT) · `기능정의서_HOME`(F-001~F-077) · `기능명세서_v2.0`(페이지별 상세) · `HOME_유저플로우`(8개 시나리오) · `FACILITY 구상도`(§3.5 알림·대응, §3.6 대시보드) · `완성목표_실행계획`(§2.1-6 프런트 전환, §4 G3 다기기 UI·권한)

---

## 2. 전환 작업 목록

명세 §4의 Phase 순서 그대로. **아무것도 시작하지 않았다.**

### Phase 0 — 계약 연결 + 환경변수

| 작업                     | 현재 위치                      | 내용                                                                                                                                                                                                       |
| ------------------------ | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BACKEND_URL` → 환경변수 | `src/lib/backend.ts:6`         | `"http://127.0.0.1:8000"` 하드코딩. **프론트↔백엔드의 유일한 결합점.** `import.meta.env.VITE_API_BASE_URL`로                                                                                               |
| WS 주소 분리             | `src/lib/backend.ts:236`       | 현재 `BACKEND_URL.replace(/^http/, "ws") + "/ws/live"`로 파생. `VITE_WS_URL`로                                                                                                                             |
| 타입 코드젠              | `src/lib/backend.ts` 타입 18종 | `LiveSample`·`CalibrationStatus`·`MonitorStatus`·`PresenceConfigSnapshot` 등 손으로 미러링 → `packages/contracts/api.py`(백엔드 레포)의 OpenAPI에서 `src/api/generated/`로 생성. **백엔드 Phase 0에 의존** |

### Phase 1 — 데이터 계층 전환 (★ 이 레포의 본체)

| 작업                                   | 내용                                                                                                                                                                                                                                                                   |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 도메인 타입 분리 → `src/lib/domain.ts` | `mock-store.ts`에서 `StateMachine`, `Service`, `Role`, `Presence`, `CalibrationStage`, `PipelineConfig`, `Facility`, `UserAccount`, `Session`, `Device`, `Resident`, `FallEvent`, `Recipient`, `EventLogEntry`, `SignupInput` 분리. 목업과 실데이터가 같은 타입을 쓰게 |
| 포매터 분리 → `src/lib/format.ts`      | `fmtTime`, `stateLabel/Color`, `presenceLabel/Color` …                                                                                                                                                                                                                 |
| 읽기 경로 교체                         | `useStore(s => s.devices)` → `useDevices()` 등 (명세 §4 Phase 1-2 표). **스코핑은 서버로** — 클라이언트 필터는 보안이 아니다                                                                                                                                           |
| 쓰기 경로 교체                         | `upsertResident` / `upsertDevice` / `updateResponse` / `updateConfig` / `upsertRecipient` / `removeMember` / `regenerateInviteCode` → `useMutation` + 낙관적 업데이트 + 무효화. `upsertResident`의 **대표 장치 정규화**는 서버로 옮기거나 양쪽에서 보장                |
| 시뮬레이션 격리                        | `mock-store.ts`의 `setInterval(tick, 100)`이 **모듈 최상위에서 무조건 실행**된다. `import.meta.env.DEV && VITE_USE_MOCK === "1"`로 분기해 프로덕션 번들에서 죽은 코드가 되게                                                                                           |

### Phase 2 — 인증 · 권한

| 작업                       | 현재                                                                                                          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------- |
| JWT access + refresh       | `localStorage["csi-guard-session"]` = `{userId}`                                                              |
| 401 시 자동 로그아웃       | 없음                                                                                                          |
| 서버 인가 + UI 가드 이중화 | `facility-members`만 `role !== "ROOT"` 리다이렉트. `/config`는 MEMBER에게 경고 배너만, Apply 버튼은 막지 않음 |
| 죽은 코드 제거             | `AuthGate.tsx`의 `/onboarding` 경로 체크 — 해당 라우트가 없어 항상 거짓                                       |

`hydrated` 게이트(SSR 불일치 방지)는 **유지**.

### Phase 3 — 실시간 다기기 구독

현재 `backend.ts`의 WS는 **모듈 레벨 싱글턴 하나**(`sharedWs` + `sharedListeners`), 히스토리 300 샘플, **1기기 전제**. FACILITY는 다수 클라이언트 × 다수 기기다.

| 작업                                    | 내용                                                                                           |
| --------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `src/api/realtime/socket.ts`            | 연결은 하나로 유지, **기기별 구독/해제 메시지** (현재 클라이언트→서버 프로토콜 없음)           |
| `src/api/realtime/useLiveStream.ts`     | `useLiveStream(deviceId)` / `useLiveStreams(deviceIds)`                                        |
| `BackendDetectionBridge` → `LiveBridge` | `user?.service !== "HOME"`이면 `null` 반환하는 **HOME 전용 제약 제거.** 3초 마운트 유예는 유지 |

### Phase 4 — 신규/전환 화면

| ID    | 화면                        | 현재                                                                                    | 목표                                                                                                                                                 |
| ----- | --------------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| F-W07 | `devices.tsx` MQTT 카드     | 상시 비활성 ("로컬 시리얼 구조 확정 전까지")                                            | 기기 프로비저닝 연결. 토픽 표시 `wifiguard/{facility}/{device}/...`. 현재 `csi/home/{userId 뒷4자리}/{공간 로마자}` 자동 생성(F-034)은 서버 발급으로 |
| F-W11 | 낙상 감지 중단 경고         | HOME만 "연결 안 됨"                                                                     | **신규 · 안전 요구.** 사이드바 3상태(정상 / 낙상 감지 중단 / 완전 오프라인) + 대시보드 배너 + 이벤트 로그                                            |
| F-W08 | `history.tsx` 낙상 파형     | `fall.id` 시드 PRNG로 **재구성한 시각화**                                               | 실제 MV 시계열 (백엔드 TimescaleDB, Phase 2 이후). 그 전까지 UI에 "재구성 시각화"임을 명시                                                           |
| F-W09 | `train.tsx`                 | "Coming Soon" 정적 스텁 (41줄)                                                          | MLflow 연동 — 모델 버전 · 성능 비교 · 드리프트 추세 · 롤백                                                                                           |
| F-W12 | `devices.tsx` 드리프트 추세 | 없음                                                                                    | 시계열 표시 (G5)                                                                                                                                     |
| —     | `notifications.tsx`         | HOME은 실제 ntfy 수신자 테이블, FACILITY는 항상 "ACTIVE"인 **장식용** SMS/Push/ARS 카드 | SMS가 실동작하므로 실기능으로. **알려진 동작**: HOME 사용자는 실백엔드가 꺼져 있어도 항상 `RealNtfySection`이 보인다(폴백 분기 미도달) — 정리        |
| —     | 캘리브레이션 UI             | 목업 타이머와 실백엔드 폴링이 **같은 `Device` 필드에 쓰기** 때문에 렌더링 통합됨        | **이 구조 유지.** 개발용 폴백이 그대로 산다                                                                                                          |

---

## 3. 유의할 코드 구조

- **`index.tsx`(626줄)가 공용 컴포넌트를 내보낸다**: `Header`, `SectionTitle`, `ResponseBadge`를 다른 모든 페이지가 import한다. 리팩터 시 `components/`로 옮길 것.
- **`devices.tsx`(1,002줄)**가 가장 무겁고 백엔드 결합이 가장 강하다. Phase 1·4의 주요 작업 지점.
- **`FallAlarmModal`은 자동 타임아웃이 없다.** 이전에 30초 후 `PENDING`으로 남기고 닫히던 동작은 제거되었다. 안전 기능이므로 **이 정책을 유지**한다.
- **SSR 오류 처리(`start.ts`, `server.ts`, `error-capture.ts`, `error-page.ts`)는 특정 upstream h3 동작에 대한 우회다.** 제거하지 말 것.
- **`routeTree.gen.ts`는 자동생성.** 손으로 고치지 말 것.
- **`bunfig.toml` 24h 공급망 가드**: 신규 패키지는 릴리스 24시간 후에야 설치된다. 예외 목록 추가는 별도 합의.

---

## 4. 미결정 사항

### 4-1. Lovable 연결 (명세 §9-4)

원본 레포는 [Lovable](https://lovable.dev)에 연결되어 있고 push가 Lovable 에디터로 동기화된다. `.lovable/`(plan.md, project.json), `src/lib/lovable-error-reporting.ts`, `@lovable.dev/vite-tanstack-config` 의존을 **일단 그대로 가져왔다.** 새 레포로 옮길지 끊고 순수 git으로 갈지 결정이 필요하다.

- 옮긴다면: Lovable 프로젝트 설정에서 연결 레포를 바꾸고, 이미 푸시된 커밋의 force-push·rebase·amend를 피할 것 (이력 유실).
- 끊는다면: `.lovable/` 삭제, `lovable-error-reporting.ts` 제거, `vite.config.ts`를 직접 구성(`@lovable.dev/vite-tanstack-config`가 주입하던 TanStack devtools·tanstackStart·viteReact·tailwindcss·tsConfigPaths·nitro·`@` 별칭을 손으로).

### 4-2. 그 외 (명세 §9)

1. **FACILITY 목업 시뮬레이션을 언제 끌 것인가.** 백엔드 FACILITY 실서비스(G3)는 8~10주 규모. 그 전까지 목업으로 데모해야 한다.
2. **낙상 이력 파형 실데이터 전환 시점.** TimescaleDB에 MV 시계열이 쌓여야 가능 (백엔드 Phase 2).
3. **테스트 체계.** Vitest + Playwright. 데이터 계층 전면 교체를 테스트 없이 하는 것은 위험하다.
4. **`simulateFall()` 실서비스 노출 여부.** 알람 경로 검증에 유용하지만 실운영에서 오알람을 만들 수 있다. 관리자 전용 + 감사 로그 vs 제거.
5. **모바일 대응.** FACILITY는 근무자 모바일 푸시·현장 확인이 전제. 반응형으로 충분한지, 별도 앱이 필요한지.

---

## 5. 검증

```bash
# 원본과 동일한가
diff -rq "../../src" src          # "Only in src: api" 한 줄만 나와야 함

# 빌드되는가 (node_modules 필요)
bun install && bun run build
```

이번 배치에서는 `diff -rq`만 확인했다. `bun install`은 하지 않았다 (node_modules 생성).
