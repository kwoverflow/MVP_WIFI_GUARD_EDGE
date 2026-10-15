// Vitest — 프론트 데이터 계층(src/api/*) 단위 테스트 전용.
// 앱 빌드는 vite.config.ts(@lovable.dev 프리셋)를 쓰고, 테스트는 이 파일만 읽는다 (플러그인 중복 방지).
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [tsconfigPaths()],
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    clearMocks: true,
    restoreMocks: true,
  },
});
