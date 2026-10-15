"""Persistent PC fixture for the real Pi5 application, never a Pi5 benchmark.

Own loopback PostgreSQL cluster, unchanged built UI/API/model/calibration/relay.
Only sensor input, systemd and cgroups have PC adapters. No remote credentials,
Docker/Kafka/MLOps, automatic real notifications, firmware writes or production databases.
Optional, explicitly authorized mobile TEST pushes are bounded to one hour.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

import psutil

from .control import ControlAgent, LoopbackClient, read_json
from .metrics import atomic_json
from .notification_capture import NotificationCapture

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIRECTORY = ROOT / "WIFIGUARD-LOCAL-EDGE/local-output/pc-pi5-demo"
EMAIL = "pi5-demo@example.invalid"
# Disposable loopback-only demo login, never used as an operating credential.
PASSWORD = "Pi5-Local-Demo-2026!"


def mobile_test_environment(args, *, now=None):
    """Server-only credential path, opt-in, no automatic fall messages or ambient flags."""
    if not args.mobile_test_push:
        return {}
    project = args.fcm_project_id or ""
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", project) or args.fcm_key_file is None:
        raise ValueError("Mobile test requires an explicit project ID and private server key file")
    path = args.fcm_key_file.resolve()
    if not path.is_file() or path.is_relative_to(ROOT) or path.stat().st_size > 16384:
        raise ValueError("Mobile test key must be a private JSON file outside the repository")
    try:
        key = json.loads(path.read_text(encoding="utf-8"))
        valid = (key.get("type") == "service_account" and key.get("project_id") == project
                 and key.get("token_uri") == "https://oauth2.googleapis.com/token" and key.get("private_key")
                 and str(key.get("client_email", "")).endswith(f"@{project}.iam.gserviceaccount.com"))
    except Exception:
        raise ValueError("Mobile test credential metadata is invalid") from None
    if not valid:
        raise ValueError("Mobile test credential must match the selected Firebase project")
    return {"MOBILE_PUSH_ENABLED": "1", "MOBILE_PC_TEST_PUSH_ENABLED": "1",
            "MOBILE_PC_TEST_PUSH_EXPIRES_AT": str((time.time() if now is None else now) + 3600),
            "FCM_PROJECT_ID": project, "FCM_SERVICE_ACCOUNT_FILE": str(path)}


def owned_stop(process):
    if not process or process.poll() is not None:
        return
    try:
        parent = psutil.Process(process.pid)
        family = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        return
    for member in family:
        try:
            member.terminate()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(family, timeout=5)
    for member in remaining:
        try:
            member.kill()
        except psutil.NoSuchProcess:
            pass


class PcProcessMonitor:
    """Aligned unique-PID process RSS; Windows has no cgroup/PSS guarantee."""
    def __init__(self, directory, roots):
        self.directory, self.roots = Path(directory), roots
        self.previous = {}

    def sample(self):
        now = time.monotonic()
        groups, errors, seen, new = {}, {}, set(), {}
        cores = psutil.cpu_count() or 1
        for name, pid in self.roots.items():
            family = []
            cpu, rss, read, write = 0, 0, 0, 0
            warm = False
            if pid is None and name == "edge":
                groups[name] = dict(unit="PC process (stopped)", cpu_percent_one_core_basis=0,
                                    cpu_percent_host_basis=0, memory_mib=0, swap_mib=None,
                                    io_read_bytes_total=0, io_write_bytes_total=0)
                continue
            try:
                if not isinstance(pid, int) or pid <= 0:
                    raise ValueError("missing owned process ID")
                parent = psutil.Process(pid)
                family = [parent] if name == "control_monitor_inboxes" else [parent, *parent.children(recursive=True)]
                for member in family:
                    if member.pid in seen:
                        continue
                    seen.add(member.pid)
                    identity = name, member.pid, member.create_time()
                    used = sum(member.cpu_times()[:2])
                    old = self.previous.get(identity)
                    new[identity] = now, used
                    if old and now > old[0] and used >= old[1]:
                        cpu += (used - old[1]) / (now - old[0]) * 100
                    else:
                        warm = True
                    rss += member.memory_info().rss
                    io = member.io_counters()
                    read += io.read_bytes
                    write += io.write_bytes
                groups[name] = dict(unit="PC owned process tree", cpu_percent_one_core_basis=None if warm else cpu,
                                    cpu_percent_host_basis=None if warm else cpu / cores, memory_mib=rss / 2**20,
                                    swap_mib=None, io_read_bytes_total=read, io_write_bytes_total=write)
            except (psutil.Error, OSError, TypeError, ValueError) as exc:
                errors[name] = type(exc).__name__ + ": owned process unavailable"
        self.previous = new
        complete = not errors and all(v["cpu_percent_host_basis"] is not None for v in groups.values())
        total_cpu = sum(v["cpu_percent_one_core_basis"] for v in groups.values()) if complete else None
        disk = shutil.disk_usage(self.directory)
        value = {"updated_at_unix": time.time(), "fresh": True, "complete": complete,
                 "scope": "PC Edge + API + built UI + PostgreSQL + gateway + control/monitor/local inboxes",
                 "memory_method": "unique-PID RSS sum; shared pages may be counted more than once, not Pi5 cgroup usage",
                 "cpu_count": cores, "groups": groups, "errors": errors,
                 "total": {"cpu_percent_one_core_basis": total_cpu,
                           "cpu_percent_host_basis": None if total_cpu is None else total_cpu / cores,
                           "memory_mib": sum(v["memory_mib"] for v in groups.values()) if complete else None,
                           "swap_mib": None},
                 "system": {"temperature_c": None, "ram_available_mib": psutil.virtual_memory().available / 2**20,
                            "disk_used_gib": disk.used / 2**30, "disk_free_gib": disk.free / 2**30},
                 "model_gpu_used": False, "gpu_system_utilization": None, "physical_pi5": False}
        atomic_json(self.directory / "stack-status.json", value)
        return value


class PcDemo:
    def __init__(self, args):
        self.args, self.directory = args, args.output_dir.resolve()
        self.processes, self.logs = {}, []
        self.stopping = threading.Event()
        self.capture = None
        self.db_started = False
        self.owns_instance = False
        self.env = {}
        self.opener = build_opener(ProxyHandler({}))

    def launch(self, name, command, *, cwd=ROOT, extra=None):
        logfile = (self.directory / f"{name}.log").open("a", encoding="utf-8")
        self.logs.append(logfile)
        process = subprocess.Popen(command, cwd=cwd, env={**self.env, **(extra or {})},
                                   stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.processes[name] = process
        return process

    def request(self, route, data=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(f"http://127.0.0.1:{self.args.api_port}{route}",
                          None if data is None else json.dumps(data).encode(), headers)
        with self.opener.open(request, timeout=5) as response:
            return json.load(response)

    def database(self):
        import psycopg
        from psycopg import sql
        args, data = self.args, self.directory / "postgres-data"
        required = ["initdb", "pg_ctl", "postgres"]
        for name in required:
            if not (args.postgres_bin / (name + (".exe" if os.name == "nt" else ""))).is_file():
                raise ValueError("Pass --postgres-bin with native PostgreSQL binaries; see PC README")
        password_file = self.directory / "db-password.txt"
        if not data.exists():
            password_file.write_text(secrets.token_urlsafe(36), encoding="ascii")
            password_file.chmod(0o600)
            subprocess.run([str(args.postgres_bin / "initdb"), "-D", str(data), "-U", "wifiguard_pc",
                            "--encoding=UTF8", "--locale=C", "--auth=scram-sha-256", "--pwfile", str(password_file)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not password_file.is_file():
            raise ValueError("Isolated PC database password missing; will not touch the cluster")
        password = password_file.read_text(encoding="ascii")
        # A persistent Windows postgres child can retain pg_ctl's PIPE handle.
        # Log to files instead of waiting forever for that inherited pipe EOF.
        with (self.directory / "database-start.log").open("a", encoding="utf-8") as db_log:
            subprocess.run([str(args.postgres_bin / "pg_ctl"), "-D", str(data), "-l", str(self.directory / "postgres.log"),
                            "-o", f"-p {args.db_port} -h 127.0.0.1", "-w", "start"], check=True,
                           stdout=db_log, stderr=db_log, timeout=35)
        self.db_started = True
        admin = f"host=127.0.0.1 port={args.db_port} user=wifiguard_pc password={password} dbname=postgres"
        names = ("wifiguard_pc_demo", "wifiguard_pc_presence")
        with psycopg.connect(admin, autocommit=True) as connection:
            for name in names:
                if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        dsn = admin.replace("dbname=postgres", "dbname=" + names[0])
        ts_dsn = admin.replace("dbname=postgres", "dbname=" + names[1])
        with psycopg.connect(ts_dsn) as connection:
            connection.execute((ROOT / "WIFIGUARD-LOCAL-EDGE/deploy/pc/init-presence.sql").read_text(), prepare=False)
        self.env.update(DATABASE_URL=f"postgresql+psycopg://wifiguard_pc:{password}@127.0.0.1:{args.db_port}/{names[0]}",
                        PG_DSN=dsn, TSDB_DSN=ts_dsn)
        return int((data / "postmaster.pid").read_text().splitlines()[0])

    def edge_action(self, action):
        if action in ("stop", "restart"):
            owned_stop(self.processes.get("edge"))
            self.processes.pop("edge", None)
        if action in ("start", "restart") and self.edge_state() != "active":
            # Every start has a fresh nonce and the unchanged 30+30 s calibration.
            atomic_json(self.directory / "simulation-scenario.json", {"scenario": "empty", "updated_at_unix": time.time()})
            self.launch("edge", [sys.executable, "-m", "wifiguard_local_edge.runtime", "--backend", "onnx",
                        "--model", str(self.args.model.resolve()), "--transport", "replay", "--replay-source", "synthetic-wgsp",
                        "--simulation-scenario-file", str(self.directory / "simulation-scenario.json"),
                        "--startup-calibration", "empty-room", "--stride-seconds", ".5", "--threads", "1",
                        "--presence-stride-seconds", ".5", "--presence-wander-stride-seconds", "1",
                        "--model-carrier-selection", "q-top30", "--backend-url", f"http://127.0.0.1:{self.args.api_port}",
                        "--output-dir", str(self.directory)])

    def edge_state(self):
        process = self.processes.get("edge")
        return "inactive" if process is None else "active" if process.poll() is None else "failed"

    def start(self):
        args = self.args
        mobile_env = mobile_test_environment(args)
        old = read_json(self.directory / "demo-instance.json")
        try:
            if old.get("running") and psutil.Process(old["pid"]).create_time() == old["created_at"]:
                raise ValueError("This PC demo is already running; use --stop or its existing UI")
        except (psutil.NoSuchProcess, KeyError):
            pass
        for port in (args.port, args.api_port, args.ui_port, args.db_port):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))  # fail before touching an existing listener
        marker = read_json(ROOT / "WIFIGUARD-FRONTEND/.output/pi5-build.json")
        if marker.get("api_base") != "same-origin" or marker.get("mock") is not False or marker.get("pc_simulation") is not True:
            raise ValueError("Build the PC same-origin UI first: node scripts/build-pi5.mjs --pc")
        if not args.model.is_file() or not args.backend_python.is_file():
            raise ValueError("Existing FP32 model and backend virtualenv are required")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                    "MQTT_HOST": "", "KAFKA_BOOTSTRAP": "", "ALLOW_FALL_SIMULATE": "0",
                    "LOCAL_EDGE_TOKEN": secrets.token_urlsafe(48), "JWT_SECRET": secrets.token_urlsafe(48),
                    "PI5_CONTROL_ENABLED": "1", "PC_SIMULATION_MODE": "1", "PI5_STATE_DIR": str(self.directory),
                    "MOBILE_PUSH_ENABLED": "0", "MOBILE_PC_TEST_PUSH_ENABLED": "0",
                    "MOBILE_PC_TEST_PUSH_EXPIRES_AT": "", "FCM_PROJECT_ID": "", "FCM_SERVICE_ACCOUNT_FILE": "",
                    "DEVICE_OFFLINE_AFTER_S": "5", "DB_POOL_SIZE": "3", "DB_MAX_OVERFLOW": "1",
                    "PRESENCE_BATCH_SIZE": "40", "PRESENCE_FLUSH_INTERVAL_S": "1",
                    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        for key in list(self.env):
            if key.startswith(("SMTP_", "LOCAL_DEVICE", "LOCAL_TENANT")):
                self.env.pop(key)
        source_dirs = [ROOT / p / "src" for p in ("WIFIGUARD-LOCAL-EDGE", "WIFIGUARD-RASPBERRY",
                       "WIFIGUARD-BACKEND/packages/contracts", "WIFIGUARD-BACKEND/packages/db",
                       "WIFIGUARD-BACKEND/services/api", "WIFIGUARD-BACKEND/services/ingest",
                       "WIFIGUARD-BACKEND/services/notification", "WIFIGUARD-BACKEND/services/csi-fall-segmentation")]
        self.env["PYTHONPATH"] = os.pathsep.join(map(str, source_dirs))
        database_pid = self.database()
        self.capture = NotificationCapture()
        self.env.update(SMTP_HOST="127.0.0.1", SMTP_PORT=str(self.capture.smtp.server_address[1]),
                        SMTP_STARTTLS="0", SMTP_USERNAME="", SMTP_PASSWORD="", SMTP_FROM="pc-demo@example.invalid",
                        PC_SIMULATION_PUSH_URL=f"http://127.0.0.1:{self.capture.push.server_address[1]}")
        backend = ROOT / "WIFIGUARD-BACKEND"
        with (self.directory / "migration.log").open("a", encoding="utf-8") as log:
            subprocess.run([str(args.backend_python), "-m", "alembic", "-c", str(backend / "packages/db/alembic.ini"),
                            "upgrade", "head"], cwd=backend, env=self.env, stdout=log, stderr=log, check=True)
        self.launch("api", [str(args.backend_python), "-m", "uvicorn", "wifiguard_api.app:app",
                           "--host", "127.0.0.1", "--port", str(args.api_port), "--workers", "1", "--no-proxy-headers"], cwd=backend,
                    extra=mobile_env)
        for _ in range(100):
            try:
                self.request("/health")
                break
            except Exception:
                if self.processes["api"].poll() is not None:
                    raise RuntimeError("PC API failed; see api.log")
                self.stopping.wait(.2)
        else:
            raise RuntimeError("PC API did not start")
        account = read_json(self.directory / "demo-account.json")
        if not account:
            login = self.request("/api/v1/auth/signup", {"email": EMAIL, "password": PASSWORD,
                                                       "name": "Pi5 PC 모의검증", "service": "HOME"})
            token = login["accessToken"]
            device = self.request("/api/v1/devices", {"name": "PC 모의 ESP32-C5", "room": "모의 시험방", "connection": "SPI"}, token)
            resident = self.request("/api/v1/residents", {"name": "모의 시험 대상", "room": "모의 시험방", "deviceIds": [device["id"]]}, token)
            account = {"device_id": device["id"], "tenant_id": f"home-{login['user']['id']}", "resident_id": resident["id"]}
            atomic_json(self.directory / "demo-account.json", account)
        else:
            token = self.request("/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})["accessToken"]
        atomic_json(self.directory / "device-binding.json", {k: account[k] for k in ("device_id", "tenant_id")})
        self.env.update(LOCAL_DEVICE_ID=account["device_id"], LOCAL_TENANT_ID=account["tenant_id"])
        recipients = self.request("/api/v1/recipients", token=token)
        payload = {"name": "PC 로컬 알림 수신기", "role": "FAMILY", "residentId": account["resident_id"],
                   "email": "pc-demo@example.invalid", "emailEnabled": True, "push": True,
                   "ntfyTopic": "validation-only", "ntfyServer": self.env["PC_SIMULATION_PUSH_URL"], "enabled": True}
        if recipients:
            req = Request(f"http://127.0.0.1:{args.api_port}/api/v1/recipients/{recipients[0]['id']}", json.dumps(payload).encode(),
                          {"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="PATCH")
            self.opener.open(req, timeout=5).close()
        else:
            self.request("/api/v1/recipients", payload, token)
        self.token = token
        self.launch("ui", [args.node, "--max-old-space-size=192", "scripts/serve-pi5.mjs"], cwd=ROOT / "WIFIGUARD-FRONTEND", extra={"UI_PORT": str(args.ui_port)})
        self.launch("gateway", [args.node, "scripts/serve-pc-gateway.mjs"], cwd=ROOT / "WIFIGUARD-FRONTEND",
                    extra={"PC_GATEWAY_PORT": str(args.port), "PC_API_PORT": str(args.api_port), "PC_UI_PORT": str(args.ui_port)})
        self.agent = ControlAgent(self.directory, LoopbackClient(f"http://127.0.0.1:{args.api_port}", self.env["LOCAL_EDGE_TOKEN"]),
                                  executor=self.edge_action, state_reader=self.edge_state)
        self.monitor = PcProcessMonitor(self.directory, {})
        self.database_pid = database_pid
        (self.directory / "stop.request").unlink(missing_ok=True)
        atomic_json(self.directory / "demo-instance.json", {"pid": os.getpid(), "created_at": psutil.Process().create_time(),
                    "running": True, "url": f"http://127.0.0.1:{args.port}", "started_at_unix": time.time()})
        self.owns_instance = True
        if args.auto_start:
            self.edge_action("start")
        print(f"PC_PI5_DEMO_READY http://127.0.0.1:{args.port}\nLogin: {EMAIL}\nDemo-only password: {PASSWORD}", flush=True)

    def cycle(self):
        self.agent.cycle()
        self.monitor.roots = {name: process.pid for name, process in self.processes.items() if name != "edge"}
        self.monitor.roots.update(edge=self.processes["edge"].pid if self.edge_state() == "active" else None,
                                  database=self.database_pid, control_monitor_inboxes=os.getpid())
        self.monitor.sample()
        scenario = read_json(self.directory / "simulation-scenario.json")
        health = self.request("/health")
        atomic_json(self.directory / "pc-simulation-status.json", {"enabled": True, "updated_at_unix": time.time(),
                    "scenario": scenario.get("scenario", "empty"), "hardware_simulated": True,
                    "model_mocked": False, "physical_pi5": False, "mlops_enabled": False,
                    "database_engine": "native PostgreSQL (Timescale hypertable/retention not tested)",
                    "notification": self.capture.status(), "ingest": health.get("ingest"),
                    "source_rate_hz": 320, "model_stride_seconds": .5, "presence_stride_seconds": .5,
                    "psd_stride_seconds": 1, "absence_grace_seconds": 10})

    def close(self):
        for name in ("edge", "gateway", "ui", "api"):
            owned_stop(self.processes.get(name))
        if self.capture:
            self.capture.stop()
        if self.db_started:
            subprocess.run([str(self.args.postgres_bin / "pg_ctl"), "-D", str(self.directory / "postgres-data"),
                            "-m", "fast", "-w", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        for log in self.logs:
            log.close()
        if self.owns_instance:
            atomic_json(self.directory / "demo-instance.json", {"running": False, "stopped_at_unix": time.time()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--postgres-bin", type=Path, default=Path(os.getenv("TEMP", "/tmp")) / "wifiguard-pg16-test-tools/pgsql/bin")
    parser.add_argument("--backend-python", type=Path, default=ROOT / "WIFIGUARD-BACKEND/.venv/Scripts/python.exe")
    parser.add_argument("--model", type=Path, default=ROOT / "WIFIGUARD-LOCAL-EDGE/local-output/pi5-model-20261001/model-fp32.onnx")
    parser.add_argument("--node", default="node")
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--api-port", type=int, default=18095)
    parser.add_argument("--ui-port", type=int, default=14195)
    parser.add_argument("--db-port", type=int, default=55483)
    parser.add_argument("--auto-start", action="store_true")
    parser.add_argument("--mobile-test-push", action="store_true", help="Explicitly allow manual TEST pushes only for one hour")
    parser.add_argument("--fcm-project-id")
    parser.add_argument("--fcm-key-file", type=Path)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)
    if args.stop:
        if not read_json(args.output_dir / "demo-instance.json").get("running"):
            parser.error("PC demo is not running")
        atomic_json(args.output_dir / "stop.request", {"created_at_unix": time.time()})
        print("PC demo stop requested; isolated database and history are retained")
        return
    if len({args.port, args.api_port, args.ui_port, args.db_port}) != 4 or any(not 1024 <= p <= 65535 for p in (args.port, args.api_port, args.ui_port, args.db_port)):
        parser.error("four distinct non-privileged ports required")
    demo = PcDemo(args)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: demo.stopping.set())
    try:
        demo.start()
        while not demo.stopping.wait(1):
            if (demo.directory / "stop.request").exists():
                break
            demo.cycle()
    finally:
        demo.close()


if __name__ == "__main__":
    main()
