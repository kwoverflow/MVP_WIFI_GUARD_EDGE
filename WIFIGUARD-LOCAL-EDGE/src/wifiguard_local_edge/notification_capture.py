"""Loopback-only test inboxes; original SMTP and HTTP adapters still execute.

Not a phone push server or an Internet mail-delivery test. No real recipient
credentials are used. The test controller, not the Pi5 service, runs these.
"""
import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class NotificationCapture:
    def __init__(self):
        self.email_count = self.push_count = 0
        self._lock = threading.Lock()
        owner = self

        class SMTP(socketserver.StreamRequestHandler):
            def handle(self):
                def reply(value):
                    self.wfile.write(value.encode() + b"\r\n")
                    self.wfile.flush()
                reply("220 localhost validation-only")
                while True:
                    line = self.rfile.readline(65536)
                    if not line:
                        return
                    verb = line.split(b" ", 1)[0].strip().upper()
                    if verb in (b"EHLO", b"HELO"):
                        reply("250 localhost")
                    elif verb in (b"MAIL", b"RCPT", b"RSET", b"NOOP"):
                        reply("250 OK")
                    elif verb == b"DATA":
                        reply("354 End with dot")
                        size = 0
                        while True:
                            data = self.rfile.readline(65536)
                            if not data or data == b".\r\n":
                                break
                            size += len(data)
                            if size > 1024 * 1024:
                                return
                        if data:
                            with owner._lock:
                                owner.email_count += 1
                            reply("250 Captured locally")
                    elif verb == b"QUIT":
                        reply("221 Bye")
                        return
                    else:
                        reply("502 Unsupported validation command")

        class Push(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size < 1024 * 1024:
                    self.send_error(400)
                    return
                message = json.loads(self.rfile.read(size))
                if message.get("topic") != "validation-only":
                    self.send_error(400)
                    return
                with owner._lock:
                    owner.push_count += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTP)
        self.smtp.daemon_threads = True
        self.push = ThreadingHTTPServer(("127.0.0.1", 0), Push)
        for server in (self.smtp, self.push):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def status(self):
        with self._lock:
            return dict(email_captured=self.email_count, push_http_captured=self.push_count,
                        real_email_or_phone_delivery=False)

    def stop(self):
        for server in (self.smtp, self.push):
            server.shutdown()
            server.server_close()
