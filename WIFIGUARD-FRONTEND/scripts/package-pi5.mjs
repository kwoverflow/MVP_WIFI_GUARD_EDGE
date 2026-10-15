// Export generated bundles only; never copies node_modules, env files or tokens.
import { cp, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
if (!process.argv[2]) throw new Error("Usage: node scripts/package-pi5.mjs NEW_OUTPUT_DIRECTORY");
const output = resolve(process.argv[2]);
try {
  await stat(output);
  throw new Error("Output already exists; refusing to replace it");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const build = JSON.parse(await readFile(resolve(root, ".output/pi5-build.json"), "utf8"));
if (build.kind !== "pi5-local-ui" || build.api_base !== "same-origin" || build.mock !== false)
  throw new Error("Run node scripts/build-pi5.mjs first: same-origin real API build required");
for (const source of ["dist/server/server.js", ".output/server/index.mjs", ".output/public"]) {
  await stat(resolve(root, source));
}
await mkdir(output, { recursive: true });
for (const source of ["dist/server", ".output", "scripts/serve-pi5.mjs", ...(build.pc_simulation ? ["scripts/serve-pc-gateway.mjs"] : [])]) {
  const destination = resolve(output, source);
  await mkdir(dirname(destination), { recursive: true });
  await cp(resolve(root, source), destination, {
    recursive: true,
    errorOnExist: true,
    force: false,
  });
}
await writeFile(
  resolve(output, "package.json"),
  JSON.stringify({ private: true, type: "module" }) + "\n",
  { flag: "wx" },
);
console.log("PI5_UI_BUNDLE_EXPORTED: run node scripts/serve-pi5.mjs, no npm install on Pi5");
