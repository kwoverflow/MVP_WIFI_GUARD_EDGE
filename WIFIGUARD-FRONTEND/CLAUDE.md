# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

WIFI-GUARD frontend: a Wi-Fi CSI-based non-contact fall detection dashboard (실시간 관제 · 낙상 감지). This repo was split out of the original `Guardian Angel Alert` monorepo on 2026-09-03 — see [PORTING.md](PORTING.md) for what moved and what's pending. The Python backend that used to live alongside this code is now a separate repo (`WIFIGUARD-BACKEND`); the edge/Raspberry Pi code is `WIFIGUARD-RASPBERRY`.

**The UI is complete and, as of 2026-09-08, every route reads/writes the real service API** (`WIFIGUARD-BACKEND`, `/api/v1`) through TanStack Query. The in-memory mock store (`src/lib/mock-store.ts`) survives only as a demo fallback behind `VITE_USE_MOCK=1`. See `docs/WIFI-GUARD_레포명세_frontend_v1.0_20260804.md` for the original phased plan and `../REVIEW_20260907.md` §9 for what was built.

Current state of the data layer:

- **Real mode (default)**: `src/api/client.ts` (fetch wrapper, `VITE_API_BASE_URL`, Bearer access token in memory, refresh token in `localStorage["csi-guard-refresh"]`, 401 → refresh once → logout), `src/api/auth.ts` (`useAuth()` external store: `loading | authed | anon`), `src/api/queries/index.ts` + `src/api/mutations/index.ts` (one hook per resource; every mutation invalidates its resource + `event-logs`), `src/api/generated/openapi.ts` (codegen from the backend's `packages/contracts/openapi.json` via `bun run api:types` — never hand-edit), `src/lib/domain.ts` (re-exports the generated types + UI constants).
- **Mock mode** (`VITE_USE_MOCK=1`): the same hooks are swapped at module load (`import.meta.env.VITE_USE_MOCK === "1" ? mock.useX : useXReal`) for `src/api/mock/hooks.ts`, which reads/writes `mock-store.ts` through `src/api/mock/adapters.ts` (snake/epoch-ms ↔ camel/ISO). The `setInterval(tick, 100)` simulation runs only in this mode; the default production bundle contains no mock code (verified by building and grepping for seed strings).
- **Realtime is deferred**: there is no `/ws/live` yet. Resident runtime fields (`state`, `mv`, `presence`, `online`, …) come back `null` and the UI shows **"감지 미동작"** (F-W11 3-state: ACTIVE / INACTIVE / OFFLINE via `useDetectionStatus()`). `null` means "fall detection is not running", never "no fall". `VITE_ENABLE_LIVE=1` re-enables the legacy local-backend path (`src/lib/backend.ts`, `BackendDetectionBridge`, `components/LegacyLivePanels.tsx`) for development against `main.py`.

The UI text is Korean (product is being built for a Korean eldercare/home-monitoring market). Keep new user-facing strings in Korean and consistent with existing terminology (see "Domain terms" below).

This code was connected to [Lovable](https://lovable.dev) in the original repo. Whether that connection moves to this repo is an open decision (`.lovable/` is carried along; see PORTING.md).

## Commands

Package manager is **bun** (`bun.lock`, `bunfig.toml` present).

```bash
bun install        # install deps
bun run dev         # vite dev server
bun run build       # production build (vite build)
bun run build:dev   # dev-mode build
bun run preview     # preview a production build
bun run lint         # eslint .  (src/api/generated is ignored)
bun run format       # prettier --write .
bun run typecheck    # tsc --noEmit
bun run test         # vitest run — data-layer unit tests in src/api/__tests__ (jsdom, fetch stubbed)
bun run api:types    # regenerate src/api/generated/openapi.ts from ../WIFIGUARD-BACKEND/packages/contracts/openapi.json
```

Local stack: start the backend first (`WIFIGUARD-BACKEND`: `docker start wg-pg && make migrate && make seed && make api`), then `bun run dev` (port 8080). Demo accounts from the seed: `root@demo.io` / `member@demo.io` / `home@demo.io`, password `demo`. Copy `.env.example` to `.env` only if the API is not on `http://127.0.0.1:8000`.

Cloud stack (EC2, since 2026-09-08): port 8000 is not open to the internet, so open an SSH tunnel to the api instance (`ssh -N -L 8000:127.0.0.1:8000 ec2-user@<api-public-ip>`, see `../WIFIGUARD-BACKEND/deploy/aws/README.md` §4) and the same default URL works — don't run `make api` locally at the same time (same port).

Tests: Vitest covers `src/api/client.ts` and `src/api/auth.ts` (401→refresh→retry, refresh dedupe, bootstrap). There is no browser/E2E runner yet (Playwright is still an open decision) — route changes are verified by `typecheck` + `lint` + `build` + the manual checklist in `../REVIEW_20260907.md` §9.2.

## Architecture

### Stack

- **TanStack Start** (React 19 + TanStack Router, file-based routing) via `@lovable.dev/vite-tanstack-config`, which wraps most of `vite.config.ts` — do not manually add TanStack devtools, `tanstackStart`, `viteReact`, `tailwindcss`, `tsConfigPaths`, nitro, or the `@` path alias plugins; they're already injected by that config (see the comment at the top of [vite.config.ts](vite.config.ts)).
- Tailwind CSS v4 + shadcn/ui (`new-york` style, see [components.json](components.json)) for components in `src/components/ui`.
- `@tanstack/react-query` (`QueryClientProvider` in `__root.tsx`) is the data layer: `src/api/queries/index.ts` (reads) and `src/api/mutations/index.ts` (writes, with invalidation). Query keys live in `src/api/keys.ts`. `src/api/realtime/` is still empty (deferred). Hook return shapes are normalized to `QueryResultLike` / `MutationLike` (`src/api/types.ts`) so real and mock implementations are interchangeable.

### Routing (`src/routes/`)

File-based routing per TanStack Start conventions — see [src/routes/README.md](src/routes/README.md) for the naming rules. `src/routeTree.gen.ts` is auto-generated; never hand-edit it. The only root shell is `src/routes/__root.tsx`; don't create Next/Remix-style `pages/` or `app/` directories.

Route list: `index` (live dashboard), `history` (fall history), `event-log`, `devices` (device/MQTT management + calibration), `residents` (resident CRUD + resident↔device mapping), `facility-members` (root-only member management), `notifications` (알림 게이트웨이 — SMS/push/ARS recipients), `config` (detection algorithm thresholds), `account`, `train` (model-training page — currently a "Coming Soon" stub), `login`, `signup`.

**`index.tsx` exports shared components** (`Header`, `SectionTitle`, `ResponseBadge`) that every other page imports. The 626-line route file doubles as a shared module; moving those to `components/` is recommended when refactoring.

### Auth / session gating

`src/routes/__root.tsx` wraps every page in `AuthGate` ([src/components/AuthGate.tsx](src/components/AuthGate.tsx)), which reads `useAuth().status`: `loading` renders the same "Loading…" gate the old `hydrated` flag did (SSR always reports `loading`, so there is no hydration mismatch), `anon` redirects to `/login`, `authed` renders `AppSidebar` + `FallAlarmModal` chrome. `PUBLIC_PATHS` in `AuthGate.tsx` is the source of truth for which routes skip the auth check. Session restore on boot: refresh token in `localStorage` → `POST /auth/refresh` → `GET /auth/me`. Role gates are duplicated on the server (403) and in the UI (`/facility-members` ROOT-only redirect, `/config` Apply disabled for MEMBER).

### The mock store (`src/lib/mock-store.ts`) — demo fallback only

Only used when `VITE_USE_MOCK=1`, through `src/api/mock/hooks.ts` + `adapters.ts`; routes never import it directly (exceptions: `useTick` in `index.tsx` behind the same flag, and the legacy live panels). A hand-rolled external store using `useSyncExternalStore` (`useStore(selector)`). Domain model mirrors the server's but with snake-case RF fields and epoch-ms timestamps — the adapters translate. Still worth knowing:

- **Simulation loop**: `setInterval(tick, 100)` only starts when `import.meta.env.VITE_USE_MOCK === "1"`.
- **Fall state machine** `IDLE | SUSPECT | FALL | COOLDOWN` driven by `mv` vs `PipelineConfig.mv_threshold` — simulation-only; the real tenant config is `TenantConfig` from `GET /api/v1/config`.
- Client-side scoping by `facilityId` / `ownerUserId` exists only here; the real API scopes on the server (`deps.py` `Scope`), and route code no longer filters.
- Formatters moved to `src/lib/format.ts` (accept ISO strings and epoch ms; `null` renders as "감지 미동작"). The 61-second calibration demo timer moved to `src/lib/calibration-sim.ts`; `devices.tsx` persists its result with `PATCH /devices/{id}`.

**The mock store is not to be deleted.** Keep it importable under `VITE_USE_MOCK=1` and keep the adapters in sync when `domain.ts` changes.

### `src/lib/backend.ts` — legacy local-backend contract (kept, gated)

Hand-mirrored types and hooks for the original local FastAPI (`main.py`: REST 19 + `/ws/live`). Only `LegacyLivePanels.tsx`, `BackendDetectionBridge.tsx` and the `VITE_ENABLE_LIVE=1` branch of `index.tsx` use it now. `BACKEND_URL`/`WS_URL` read `VITE_API_BASE_URL`/`VITE_WS_URL`. The WS is a module-level singleton (`sharedWs`) that assumes **one device**; the cloud realtime path (multi-device subscription) will replace it under `src/api/realtime/`.

**`/ws/live` contract principle**: presence fields (`presence_state`, `mv_current`, `wander_current` …) are always present while the edge is connected; fall fields (`detect_state`, `proba_fall`, `threshold` …) are only present when the DL model is loaded. **Absence of fall fields means "fall detection is not running", not "no fall"** — the UI must distinguish these (spec §4-2, R6: safety issue).

### Alarm paths — keep the redundancy

Phone push (SMS/ntfy) goes `backend → ntfy/SMS → device` and **never touches this frontend**. The in-app `FallAlarmModal` subscribes to `/ws/live`'s `detect_state` — a completely separate path. They share only the trigger. `FallAlarmModal` has **no auto-timeout** (a person must respond) and is always mounted inside `AuthGate`.

### SSR error handling

`src/start.ts` (request middleware) and `src/server.ts` (fetch wrapper) both catch server-side errors and render a fallback via `src/lib/error-page.ts`, because h3/Nitro can swallow in-handler throws into an opaque `{"unhandled":true,"message":"HTTPError"}` 500 response. `src/lib/error-capture.ts` records the last real error out-of-band so `server.ts` can recover the original stack trace. **Don't remove this plumbing** — it's working around a specific upstream h3 behavior.

## Domain terms (Korean UI)

- MV / moving variance → **움직임 감지** in threshold/label text, "이동 분산 (딥러닝 입력)" when referring to the signal.
- Fall states: IDLE=대기, SUSPECT=의심, FALL=낙상, COOLDOWN=냉각중.
- Presence: PRESENT=재실, ABSENT=퇴실.
- Service types: HOME (가정) vs FACILITY (시설); FACILITY roles: ROOT(시설 등록자)/MEMBER(초대코드로 참여).
- Response states: 대기중 / 확인함 / 출동중 / 오탐지.
- **Three threshold axes that must never be conflated** (spec §5.1): `presence_mv_threshold` ("움직임 임계값", MV scale), `wander_baseline` ("재실 baseline", PSD scale), and the DL `threshold` = 0.468 ("판정 임계값"/"낙상 확률 임계값", probability). The mock's `wander_threshold` (absolute) and the real backend's `wander_ratio_threshold` (multiple of baseline) are different numbers that do not convert.

## Conventions

- Path alias `@/*` → `src/*` (see [tsconfig.json](tsconfig.json)).
- Prettier: 100 col width, double quotes, trailing commas everywhere — run `bun run format` rather than hand-wrapping.
- ESLint: `@typescript-eslint/no-unused-vars` is off and `noUnusedLocals`/`noUnusedParameters` are off in `tsconfig.json` — don't add these back speculatively. Importing the Next.js `server-only` package is blocked by lint; use a `*.server.ts` filename or `@tanstack/react-start/server-only` instead.
- `bunfig.toml` enforces a 24h supply-chain guard on new package versions (`minimumReleaseAge`); only a short allowlist of `@lovable.dev/*` packages bypasses it. Adding to that exclusion list needs explicit user confirmation.
