// PC-only loopback gateway. Same-origin production UI + real API + real WS.
// Never proxy the privileged /internal routes; no npm/dev server at runtime.
import http from "node:http";
import net from "node:net";

const port = Number(process.env.PC_GATEWAY_PORT || 8095);
const apiPort = Number(process.env.PC_API_PORT || 18095);
const uiPort = Number(process.env.PC_UI_PORT || 14195);
for (const value of [port, apiPort, uiPort])
  if (!Number.isInteger(value) || value < 1024 || value > 65535) throw new Error("Invalid PC port");
const localHost = request => ["127.0.0.1", "localhost"].includes((request.headers.host || "").split(":")[0].toLowerCase());
const server = http.createServer((request, response) => {
  if (!localHost(request)) { response.writeHead(403).end(); return; }
  let path;
  try { path = decodeURIComponent(new URL(request.url, "http://localhost").pathname); }
  catch { response.writeHead(400).end(); return; }
  if (path.startsWith("/internal")) { response.writeHead(404).end(); return; }
  const target = path.startsWith("/api/") || path === "/health" ? apiPort : uiPort;
  const headers = { ...request.headers };
  for (const key of Object.keys(headers)) if (key.startsWith("x-forwarded-") || key === "forwarded") delete headers[key];
  const upstream = http.request({ hostname: "127.0.0.1", port: target, path: request.url, method: request.method, headers }, result => {
    response.writeHead(result.statusCode, result.headers);
    result.pipe(response);
  });
  upstream.on("error", () => { if (!response.headersSent) response.writeHead(502); response.end("PC service unavailable"); });
  request.pipe(upstream);
  request.on("aborted", () => upstream.destroy());
});
server.on("upgrade", (request, socket, head) => {
  if (!localHost(request)) { socket.destroy(); return; }
  if (new URL(request.url, "http://localhost").pathname !== "/ws/live") { socket.destroy(); return; }
  const upstream = net.connect(apiPort, "127.0.0.1", () => {
    upstream.write(`${request.method} ${request.url} HTTP/1.1\r\n`);
    for (const [name, value] of Object.entries(request.headers))
      if (!name.startsWith("x-forwarded-") && name !== "forwarded") upstream.write(`${name}: ${value}\r\n`);
    upstream.write("\r\n");
    if (head.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on("error", () => socket.destroy());
  socket.on("error", () => upstream.destroy());
  socket.on("close", () => upstream.destroy());
});
server.listen(port, "127.0.0.1", () => console.log(`PC_UI_READY http://127.0.0.1:${port}`));
for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => server.close(() => process.exit(0)));
