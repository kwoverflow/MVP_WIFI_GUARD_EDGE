/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 서비스 API 베이스 URL (예: http://127.0.0.1:8000). 없으면 client.ts 의 기본값. */
  readonly VITE_API_BASE_URL?: string;
  /** /ws/live 주소. 비우면 VITE_API_BASE_URL 에서 파생한다 (http→ws). */
  readonly VITE_WS_URL?: string;
  /** "1" 이면 mock-store 시뮬레이션(setInterval tick)과 목업 데이터 훅을 사용한다. */
  readonly VITE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
