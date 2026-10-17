"""Privileged, wlan0-only NetworkManager broker. No browser-supplied commands.

Passwords are written only to root-owned NM keyfiles, never argv, status or logs.
NM's own checkpoint timer survives broker/API failure. Ethernet is never changed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ipaddress
import json
import os
import re
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

INTERFACE = "wlan0"
SOCKET = "/run/wifiguard-network/control.sock"
ACTIVE = {"connecting", "awaiting_confirmation", "committing", "rolling_back"}
NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"


class NetworkError(Exception):
    """Only fixed codes may cross the privilege boundary."""


@contextmanager
def capture_guard():
    """Serialize capture start against checkpoint creation across services."""
    import fcntl
    with open("/run/wifiguard-network/capture.lock", "rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def capture_network_check():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(8)
        conn.connect(SOCKET)
        conn.sendall(b'{"action":"status"}\n')
        with conn.makefile("rb") as stream:
            response = json.loads(stream.readline(65537))
    if not response.get("ok") or (response["data"].get("transaction") or {}).get("phase") in ACTIVE:
        raise NetworkError("CONNECTION_PENDING")


def validate_credentials(ssid, password, security):
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise NetworkError("INVALID_SSID")
    if security not in ("wpa-psk", "sae") or not isinstance(password, str):
        raise NetworkError("INVALID_SECURITY")
    if not (8 <= len(password) <= 63 and all(32 <= ord(c) <= 126 for c in password)
            or security == "wpa-psk" and re.fullmatch(r"[0-9a-fA-F]{64}", password)):
        raise NetworkError("INVALID_PASSWORD")


def keyfile(identity, ssid, password, security, hidden=False):
    validate_credentials(ssid, password, security)
    UUID(identity)
    # Decimal SSID bytes avoid keyfile string escaping / section injection.
    raw_ssid = ";".join(str(v) for v in ssid.encode("utf-8")) + ";"
    escaped = password.replace("\\", "\\\\").replace(" ", "\\s")
    return (f"[connection]\nid=WIFI-GUARD {identity}\nuuid={identity}\ntype=wifi\n"
            f"interface-name={INTERFACE}\nautoconnect=false\n\n[wifi]\nmode=infrastructure\n"
            f"ssid={raw_ssid}\nhidden={'true' if hidden else 'false'}\n\n[wifi-security]\n"
            f"key-mgmt={security}\npsk={escaped}\npsk-flags=0\n\n"
            "[ipv4]\nmethod=auto\nroute-metric=600\n\n[ipv6]\nmethod=auto\nroute-metric=600\n")


def split_terse(line):
    result, current, escaped = [], "", False
    for char in line:
        if escaped:
            current += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            result.append(current)
            current = ""
        else:
            current += char
    return result + [current]


class NetworkManager:
    def __init__(self, profiles=Path("/etc/NetworkManager/system-connections"), run=subprocess.run):
        self.profiles, self.run = Path(profiles), run

    def command(self, args, timeout=8):
        try:
            result = self.run(args, capture_output=True, text=True, timeout=timeout,
                              env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, check=False)
            if result.returncode:
                raise NetworkError("NETWORK_COMMAND_FAILED")
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise NetworkError("NETWORK_COMMAND_FAILED") from None

    def nm(self, *args, timeout=8):
        return self.command(["/usr/bin/nmcli", "--wait", "5", *args], timeout)

    def edge_stopped(self):
        value = self.command(["/usr/bin/systemctl", "show", "wifiguard-pi5-edge.service",
                              "--property=ActiveState", "--value"])
        return value in ("inactive", "failed")

    def active_uuid(self):
        value = self.nm("-g", "GENERAL.CON-UUID", "device", "show", INTERFACE)
        try:
            return str(UUID(value))
        except ValueError:
            return None

    def status(self):
        # ip reports networkd Ethernet too; NM remains confined to wlan0.
        links = json.loads(self.command(["/usr/sbin/ip", "-j", "address", "show"]))
        interfaces = []
        for link in links:
            if link.get("ifname") not in (INTERFACE, "eth0"):
                continue
            addresses = [str(ipaddress.ip_address(a["local"])) for a in link.get("addr_info", [])
                         if a.get("scope") == "global"]
            interfaces.append({"name": link["ifname"], "addresses": addresses,
                               "connected": "LOWER_UP" in link.get("flags", [])})
        identity = self.active_uuid()
        ssid, autoconnect = None, False
        if identity:
            ssid = self.nm("-g", "802-11-wireless.ssid", "connection", "show", "uuid", identity)
            autoconnect = self.nm("-g", "connection.autoconnect", "connection", "show", "uuid", identity) == "yes"
        return {"interfaces": interfaces, "ssid": ssid, "autoconnect": autoconnect,
                "edge_stopped": self.edge_stopped()}

    def scan(self):
        value = self.nm("-t", "--escape", "yes", "-f", "SSID,SIGNAL,SECURITY",
                        "device", "wifi", "list", "ifname", INTERFACE, "--rescan", "yes", timeout=18)
        networks = {}
        for line in value.splitlines():
            parts = split_terse(line)
            if len(parts) == 3 and parts[0] and parts[1].isdigit():
                ssid, strength, security = parts
                kind = "wpa-psk" if "WPA2" in security or "WPA1" in security else "sae" if "WPA3" in security else "unsupported"
                if "802.1X" in security:
                    kind = "unsupported"
                if int(strength) >= networks.get(ssid, {}).get("signal", -1):
                    networks[ssid] = {"ssid": ssid, "signal": min(100, int(strength)), "security": kind}
        return sorted(networks.values(), key=lambda n: -n["signal"])[:64]

    def checkpoint(self):
        device = self.nm("-g", "GENERAL.DBUS-PATH", "device", "show", INTERFACE)
        if not re.fullmatch(r"/org/freedesktop/NetworkManager/Devices/\d+", device):
            raise NetworkError("WIFI_UNAVAILABLE")
        # flags=0: never delete other connections created during this trial.
        value = self.command(["/usr/bin/busctl", "call", NM, NM_PATH, NM, "CheckpointCreate",
                              "aouu", "1", device, "120", "0"])
        match = re.fullmatch(r'o "(/org/freedesktop/NetworkManager/Checkpoint/\d+)"', value)
        if not match:
            raise NetworkError("CHECKPOINT_FAILED")
        return match[1]

    def checkpoint_action(self, checkpoint, action):
        if not re.fullmatch(r"/org/freedesktop/NetworkManager/Checkpoint/\d+", checkpoint):
            raise NetworkError("INVALID_CHECKPOINT")
        value = self.command(["/usr/bin/busctl", "call", NM, NM_PATH, NM, action, "o", checkpoint])
        if action == "CheckpointRollback" and not re.fullmatch(r'a\{su\} 1 "[^"]+" 0', value):
            raise NetworkError("ROLLBACK_FAILED")

    def profile_path(self, identity):
        return self.profiles / f"wifiguard-{UUID(identity)}.nmconnection"

    def add(self, identity, ssid, password, security, hidden):
        path = self.profile_path(identity)
        content = keyfile(identity, ssid, password, security, hidden)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        self.nm("connection", "load", str(path))

    def up(self, identity):
        self.command(["/usr/bin/nmcli", "--wait", "25", "connection", "up", "uuid", str(UUID(identity)),
                      "ifname", INTERFACE], timeout=30)

    def save(self, identity):
        self.nm("connection", "modify", "uuid", str(UUID(identity)), "connection.autoconnect", "yes")

    def delete(self, identity):
        # Only a broker-created profile may be removed; no broad reset operations.
        path = self.profile_path(identity)
        known = self.nm("-t", "-f", "UUID", "connection", "show").splitlines()
        if identity in known:
            self.nm("connection", "delete", "uuid", str(UUID(identity)))
        path.unlink(missing_ok=True)


class Controller:
    def __init__(self, adapter, state_dir=Path("/var/lib/wifiguard-network"), clock=time.time, guard=capture_guard):
        self.adapter, self.state_dir, self.clock = adapter, Path(state_dir), clock
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock, self.transaction, self.last_scan = threading.RLock(), None, 0
        self.cached_status, self.cached_at = None, 0
        self.guard = guard
        self.recover()

    def persist(self):
        path = self.state_dir / "transaction.json"
        temp = self.state_dir / "transaction.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(self.transaction, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def recover(self):
        path = self.state_dir / "transaction.json"
        if not path.exists():
            return
        self.transaction = json.loads(path.read_text(encoding="utf-8"))
        if self.transaction and self.transaction["phase"] in ACTIVE:
            self.rollback()

    def expire(self):
        with self.lock:
            if self.transaction and self.transaction["phase"] in ACTIVE and self.clock() >= self.transaction["deadline"]:
                self.rollback()

    def rollback(self):
        with self.lock:
            tx = self.transaction
            if not tx or tx["phase"] not in ACTIVE:
                return
            tx["phase"] = "rolling_back"
            self.persist()
            try:
                self.adapter.checkpoint_action(tx["checkpoint"], "CheckpointRollback")
            except NetworkError:
                # NM may already have timed out (or rebooted). Explicitly restore
                # only the previous Wi-Fi profile; never touch Ethernet.
                if tx.get("previous"):
                    self.adapter.up(tx["previous"])
            self.adapter.delete(tx["id"])
            tx["phase"] = "rolled_back"
            self.persist()

    def connect(self, request):
        validate_credentials(request.get("ssid"), request.get("password"), request.get("security"))
        if not isinstance(request.get("hidden", False), bool):
            raise NetworkError("INVALID_REQUEST")
        with self.lock, self.guard():
            if self.transaction and self.transaction["phase"] in ACTIVE:
                raise NetworkError("CONNECTION_PENDING")
            if not self.adapter.edge_stopped():
                raise NetworkError("STOP_EDGE_FIRST")
            previous = self.adapter.active_uuid()
            checkpoint = self.adapter.checkpoint()
            identity = str(uuid4())
            self.transaction = {"id": identity, "checkpoint": checkpoint, "previous": previous,
                                "phase": "connecting", "deadline": self.clock() + 110, "error": None}
            self.persist()
        # No credentials retained on the controller or in persistent metadata.
        try:
            self.adapter.add(identity, request["ssid"], request["password"], request["security"], request.get("hidden", False))
            self.adapter.up(identity)
            with self.lock:
                if self.clock() >= self.transaction["deadline"]:
                    raise NetworkError("CONNECTION_EXPIRED")
                self.transaction["phase"] = "awaiting_confirmation"
                self.cached_status = None
                self.persist()
        except Exception:
            with self.lock:
                self.transaction["error"] = "CONNECTION_FAILED"
                self.rollback()
            raise NetworkError("CONNECTION_FAILED") from None
        return self.public_transaction()

    def public_transaction(self):
        tx = self.transaction
        return {key: tx[key] for key in ("id", "phase", "deadline", "error")} if tx else None

    def dispatch(self, request):
        if not isinstance(request, dict):
            raise NetworkError("INVALID_REQUEST")
        action = request.get("action")
        allowed = {"status": {"action"}, "scan": {"action"},
                   "connect": {"action", "ssid", "password", "security", "hidden"},
                   "confirm": {"action", "id"}, "rollback": {"action", "id"}}
        if action not in allowed or set(request) - allowed[action]:
            raise NetworkError("INVALID_REQUEST")
        if action == "status":
            if self.cached_status is None or self.clock() - self.cached_at >= 5:
                self.cached_status, self.cached_at = self.adapter.status(), self.clock()
            return {**self.cached_status, "updated_at_unix": self.cached_at,
                    "transaction": self.public_transaction()}
        if action == "connect":
            with self.lock:
                self.cached_status = None
                return {"transaction": self.connect(request)}
        with self.lock:
            self.expire()
            if action == "scan":
                if self.transaction and self.transaction["phase"] in ACTIVE:
                    raise NetworkError("CONNECTION_PENDING")
                if not self.adapter.edge_stopped():
                    raise NetworkError("STOP_EDGE_FIRST")
                if self.clock() - self.last_scan < 10:
                    raise NetworkError("SCAN_RATE_LIMITED")
                self.last_scan = self.clock()
                return {"networks": self.adapter.scan()}
            if not self.transaction or request.get("id") != self.transaction["id"]:
                raise NetworkError("STALE_TRANSACTION")
            if action == "rollback":
                self.rollback()
            elif action == "confirm":
                tx = self.transaction
                if tx["phase"] != "awaiting_confirmation" or self.clock() >= tx["deadline"]:
                    raise NetworkError("STALE_TRANSACTION")
                network = self.adapter.status()
                if self.adapter.active_uuid() != tx["id"] or not any(i["name"] == INTERFACE and i["connected"] and i["addresses"] for i in network["interfaces"]):
                    raise NetworkError("WIFI_NOT_CONNECTED")
                tx["phase"] = "committing"
                self.persist()
                try:
                    self.adapter.save(tx["id"])
                    self.adapter.checkpoint_action(tx["checkpoint"], "CheckpointDestroy")
                    tx["phase"] = "committed"
                    self.persist()
                except Exception:
                    self.rollback()
                    raise NetworkError("SAVE_FAILED") from None
            self.cached_status = None
            return {"transaction": self.public_transaction()}


def serve(controller, path=SOCKET, allowed_uid=None, stop=None):
    import pwd
    allowed_uid = pwd.getpwnam("wifiguard").pw_uid if allowed_uid is None else allowed_uid
    stop = stop or threading.Event()
    lock_path = Path(path).with_name("capture.lock")
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o660)
    os.fchmod(lock_fd, 0o660)
    os.fchown(lock_fd, 0, pwd.getpwnam("wifiguard").pw_gid)
    os.close(lock_fd)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(40)
            try:
                _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid not in (0, allowed_uid):
                    raise NetworkError("FORBIDDEN")
                raw = self.rfile.readline(4097)
                if len(raw) > 4096 or not raw.endswith(b"\n"):
                    raise NetworkError("INVALID_REQUEST")
                response = {"ok": True, "data": controller.dispatch(json.loads(raw))}
            except NetworkError as exc:
                response = {"ok": False, "error": str(exc)}
            except Exception:
                response = {"ok": False, "error": "NETWORK_UNAVAILABLE"}
            try:
                self.wfile.write(json.dumps(response, ensure_ascii=True).encode() + b"\n")
            except OSError:
                pass

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
        request_queue_size = 8

    path = Path(path)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise RuntimeError("network socket path is not a socket")
        path.unlink()
    with Server(str(path), Handler) as server:
        os.chmod(path, 0o660)
        os.chown(path, 0, pwd.getpwnam("wifiguard").pw_gid)
        server.timeout = 0.5
        last_expiry = 0
        while not stop.is_set():
            server.handle_request()
            if time.monotonic() - last_expiry >= 1:
                try:
                    controller.expire()
                except NetworkError:
                    pass  # keep retrying; pending status continues to block capture
                last_expiry = time.monotonic()
    path.unlink(missing_ok=True)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if os.geteuid() != 0:
        raise SystemExit("network agent requires root")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    serve(Controller(NetworkManager()), stop=stop)


if __name__ == "__main__":
    main()
