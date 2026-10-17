// Explicit same-origin, real API build. No secrets and no Pi5 compilation needed.
import { spawnSync } from "node:child_process";
import { writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pc = process.argv.includes("--pc");
const result = spawnSync(process.execPath, [resolve(root, "node_modules/vite/bin/vite.js"), "build"], {
  cwd: root, stdio: "inherit", shell: false,
  env: { ...process.env, VITE_PI5_LOCAL: "1", VITE_PC_SIMULATION: pc ? "1" : "0", VITE_API_BASE_URL: "", VITE_WS_URL: "", VITE_USE_MOCK: "0" },
});
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);
await writeFile(resolve(root, ".output/pi5-build.json"), JSON.stringify({
  kind: "pi5-local-ui", pc_simulation: pc, api_base: "same-origin", mock: false, built_at: new Date().toISOString(),
}) + "\n");
console.log("PI5_REAL_SAME_ORIGIN_BUILD_READY");
