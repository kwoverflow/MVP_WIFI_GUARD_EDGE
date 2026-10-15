"""Pi5 management agent: fixed systemd unit, bounded JSON commands, no shell.

Runs independently of capture so start/stop and first binding work without CSI.
HTTP credentials stay on loopback; files never contain credentials. No firmware
flash, Wi-Fi rewrite, arbitrary command, model replacement or training endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlsplit
from uuid import UUID

from .metrics import atomic_json
from .calibration_confirmation import confirm_empty_room

UNIT = "wifiguard-pi5-edge.service"
ACTIONS = {"start", "stop", "recalibrate", "confirm-empty-room"}


def read_json(path):
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("state file too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read_binding(directory, fallback_device=None, fallback_tenant=None):
    value = read_json(Path(directory) / "device-binding.json") or {
        "device_id": fallback_device or os.getenv("LOCAL_DEVICE_ID"),
        "tenant_id": fallback_tenant or os.getenv("LOCAL_TENANT_ID"),
    }
    try:
        UUID(value["device_id"])
    except (TypeError, KeyError) as exc:
        raise ValueError("register and bind a local SPI device") from exc
    if not value.get("tenant_id") or "REPLACE" in value["tenant_id"]:
        raise ValueError("register and bind a local SPI device")
    return value


class LoopbackClient:
    def __init__(self, base, token):
        parsed = urlsplit(base)
        if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("management API must use HTTP loopback")
        if len(token.encode()) < 32:
            raise ValueError("private local token required")
        self.base, self.token = base.rstrip("/"), token
        self.opener = build_opener(ProxyHandler({}))

    def request(self, route, data=None):
        request = Request(self.base + route, None if data is None else json.dumps(data, allow_nan=False).encode(),
                          {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with self.opener.open(request, timeout=2) as response:
            return json.load(response)


def unit_state():
    result = subprocess.run(["systemctl", "show", UNIT, "--property=ActiveState", "--value"],
                            check=True, capture_output=True, text=True, timeout=3)
    return result.stdout.strip()


def systemd_action(action):
    subprocess.run(["systemctl", "--no-ask-password", action, UNIT], check=True,
                   capture_output=True, timeout=40)


class ControlAgent:
    def __init__(self, directory, client, *, executor=systemd_action, state_reader=unit_state):
        self.directory, self.client = Path(directory), client
        self.executor, self.state_reader = executor, state_reader
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "commands").mkdir(exist_ok=True)
        self.binding = None
        self._last_config_at = float("-inf")

    def execute(self, value):
        identity = str(UUID(value["id"]))
        if value.get("action") not in ACTIONS or not 0 <= time.time() - value["created_at_unix"] <= 90:
            raise ValueError("invalid or expired command")
        self.binding = read_binding(self.directory)
        if (value["device_id"], value["tenant_id"]) != (self.binding["device_id"], self.binding["tenant_id"]):
            raise ValueError("command is not for the bound device")
        action = value["action"]
        if action == "confirm-empty-room":
            state = read_json(self.directory / "startup-calibration.json")
            runtime = read_json(self.directory / "status.json")
            if (state.get("run_id") != value.get("run_id") or value.get("empty_room_confirmed") is not True
                    or runtime.get("device_id") != self.binding["device_id"]
                    or state.get("run_id") != runtime.get("pi5_services", {}).get("startup_calibration", {}).get("run_id")
                    or not runtime.get("running")
                    or not 0 <= time.time() - runtime.get("updated_at_unix", 0) < 3):
                raise ValueError("stale calibration confirmation")
            confirm_empty_room(self.directory)
        else:
            # Recalibrate starts a fresh process/nonce and waits for a NEW empty
            # room confirmation. Never reuse an old confirmation after restart.
            self.executor("restart" if action == "recalibrate" else action)
        return {"id": identity, "action": action, "state": "completed", "completed_at_unix": time.time()}

    def cycle(self):
        error = None
        try:
            # Fixed allowlist; no query/user-provided paths or unit names.
            for path in sorted((self.directory / "commands").glob("*.json"), key=lambda p: p.stat().st_mtime)[:16]:
                value = read_json(path)
                try:
                    if path.stem != str(UUID(value["id"])):
                        raise ValueError("command identity mismatch")
                    result = self.execute(value)
                except Exception as exc:
                    result = {"id": value.get("id"), "action": value.get("action"), "state": "failed",
                              "error": type(exc).__name__ + ": command failed (check Pi5 journal)", "completed_at_unix": time.time()}
                atomic_json(self.directory / "last-command.json", result)
                path.unlink()
            if time.monotonic() - self._last_config_at >= 2:
                config = self.client.request("/internal/edge/config")
                self._last_config_at = time.monotonic()
                if config.get("bound"):
                    self.binding = {key: config[key] for key in ("device_id", "tenant_id")}
                    atomic_json(self.directory / "runtime-config.json", config)
                    runtime = read_json(self.directory / "status.json")
                    calibration = read_json(self.directory / "startup-calibration.json")
                    if (runtime.get("device_id") == self.binding["device_id"] and runtime.get("running")
                            and 0 <= time.time() - runtime.get("updated_at_unix", 0) < 3
                            and calibration.get("run_id") == runtime.get("pi5_services", {}).get("startup_calibration", {}).get("run_id")
                            and calibration.get("phase") not in (None, "disabled")):
                        report = {**self.binding, "calibration": calibration}
                        ack = self.client.request("/internal/edge/runtime", report)
                        if not ack.get("accepted"):
                            raise ValueError("runtime report not acknowledged")
        except Exception as exc:
            error = type(exc).__name__ + ": management sync unavailable"
        try:
            state = self.state_reader()
        except Exception:
            state = "unknown"
            error = error or "systemd state unavailable"
        atomic_json(self.directory / "control-agent.json", {"updated_at_unix": time.time(),
                    "edge_state": state, "last_error": error, "fixed_unit": UNIT})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(os.getenv("PI5_STATE_DIR", "/var/lib/wifiguard-pi5")))
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    agent = ControlAgent(args.output_dir, LoopbackClient(args.backend_url, os.environ["LOCAL_EDGE_TOKEN"]))
    stop = threading.Event()
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        agent.cycle()
        stop.wait(1)


if __name__ == "__main__":
    main()
