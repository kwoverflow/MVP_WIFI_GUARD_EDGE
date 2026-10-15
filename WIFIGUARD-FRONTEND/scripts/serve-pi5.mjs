// Run the EXISTING built SSR worker, without Vite dev server or npm at runtime.
import http from "node:http";
import { Readable } from "node:stream";
import worker from "../dist/server/server.js";

const port = Number(process.env.UI_PORT || 4173);
const server = http.createServer(async (incoming, outgoing) => {
  try {
    if (!["GET", "HEAD"].includes(incoming.method)) {
      outgoing.writeHead(405).end();
      return;
    }
    const host = incoming.headers.host || `127.0.0.1:${port}`;
    const request = new Request(`http://${host}${incoming.url}`, {
      method: incoming.method,
      headers: incoming.headers,
    });
    const response = await worker.fetch(request);
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    if (incoming.method === "HEAD" || !response.body) outgoing.end();
    else Readable.fromWeb(response.body).pipe(outgoing);
  } catch (error) {
    console.error("Pi5 UI request failed:", error.message);
    if (!outgoing.headersSent) outgoing.writeHead(500);
    outgoing.end("UI unavailable");
  }
});
server.requestTimeout = 15_000;
server.listen(port, "127.0.0.1", () => console.log(`PI5_UI_READY port=${port}`));
for (const signal of ["SIGTERM", "SIGINT"])
  process.on(signal, () => server.close(() => process.exit(0)));
