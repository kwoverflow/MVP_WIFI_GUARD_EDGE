"""Repeatable PC/native integration check; fresh isolated DBs, no real alerts.

Run with PI5_VALIDATION_ADMIN_DSN pointing to a *test* loopback PostgreSQL server
with Timescale installed. Never truncates or drops pre-existing databases.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

import psutil

from .metrics import atomic_json, distribution, host_info
from .notification_capture import NotificationCapture
from .stack_metrics import DockerDatabaseProbe, StackSampler


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_owned_processes(processes, sampler=None):
    """Also stop Windows venv launcher children; never touch DB/other services."""
    owned = {}
    for process in processes.values():
        if process.poll() is not None:
            continue
        try:
            parent = psutil.Process(process.pid)
            for member in [*parent.children(recursive=True), parent]:
                owned[member.pid] = member
        except psutil.NoSuchProcess:
            pass
    if sampler is not None:
        for name, pid, created in sampler.cpu_last:
            if name not in processes or not isinstance(pid, int):
                continue
            try:
                member = psutil.Process(pid)
                if member.create_time() == created:
                    owned[pid] = member
            except psutil.NoSuchProcess:
                pass
    for member in owned.values():
        try:
            member.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(list(owned.values()), timeout=5)
    for member in alive:
        try:
            member.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=2)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=65)
    parser.add_argument(
        "--model-stride-seconds", type=float, choices=(0.25, 0.5, 0.75, 1.0), default=0.5,
        help="model cadence only; presence stays at 0.5 s and PSD at 1 s",
    )
    parser.add_argument(
        "--cpu-affinity",
        help="same affinity for API, model/presence and UI; NOT ARM emulation",
    )
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--model-carrier-selection", choices=("training-fixed", "q-top30"), default="q-top30")
    parser.add_argument("--replay-source", choices=("synthetic", "synthetic-wgsp"), default="synthetic-wgsp")
    parser.add_argument("--database-container", help="labelled isolated Docker DB: measured, NOT physically pinned to a Windows E core")
    parser.add_argument("--database-pid", type=int, help="owned PostgreSQL root PID visible in this namespace")
    parser.add_argument("--nginx", help="optional nginx executable for the Linux full-stack fixture")
    parser.add_argument("--node", default="node")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def build_edge_command(args, backend_url, output):
    command = [
        sys.executable, "-m", "wifiguard_local_edge.runtime",
        "--backend", "onnx", "--model", str(args.model.resolve()),
        "--transport", "replay", "--replay-source", args.replay_source,
        "--model-carrier-selection", args.model_carrier_selection,
        "--presence-stride-seconds", "0.5", "--presence-wander-stride-seconds", "1",
        "--stride-seconds", str(args.model_stride_seconds),
        "--threads", str(args.threads), "--duration", str(args.duration),
        "--backend-url", backend_url, "--output-dir", str(output / "edge"),
    ]
    if args.cpu_affinity:
        command += ["--cpu-affinity", args.cpu_affinity]
    return command


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(args.database_container) == bool(args.database_pid):
        parser.error("choose exactly one DB resource scope: --database-container or --database-pid")
    if args.duration < 35:
        parser.error("at least 35 seconds required for 30 measured model windows")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    admin = os.getenv("PI5_VALIDATION_ADMIN_DSN", "")
    info = conninfo_to_dict(admin)
    if info.get("host") not in ("127.0.0.1", "localhost", "::1"):
        parser.error(
            "PI5_VALIDATION_ADMIN_DSN must be an explicitly provided loopback TEST server"
        )
    suffix = uuid4().hex[:12]
    database = "wifiguard_pi5_validation_" + suffix
    timescale = database + "_ts"
    with psycopg.connect(admin, autocommit=True) as connection:
        for name in (database, timescale):
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
            )
    dsn = make_conninfo(admin, dbname=database)
    ts_dsn = make_conninfo(admin, dbname=timescale)
    # SQLAlchemy URL handles password escaping, including user-supplied test keys.
    from sqlalchemy.engine import URL

    database_url = URL.create(
        "postgresql+psycopg",
        username=info.get("user"),
        password=info.get("password"),
        host=info["host"],
        port=int(info.get("port", 5432)),
        database=database,
    ).render_as_string(hide_password=False)
    root = Path(__file__).resolve().parents[3]
    backend, frontend = root / "WIFIGUARD-BACKEND", root / "WIFIGUARD-FRONTEND"
    with psycopg.connect(ts_dsn, autocommit=True) as connection:
        connection.execute(
            (backend / "deploy/aws/init-timescale.sql").read_text(encoding="utf-8"),
            prepare=False,
        )
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "PG_DSN": dsn,
        "TSDB_DSN": ts_dsn,
        "JWT_SECRET": secrets.token_urlsafe(48),
        "LOCAL_EDGE_TOKEN": secrets.token_urlsafe(48),
        "MQTT_HOST": "",
        "KAFKA_BOOTSTRAP": "",
        "ALLOW_FALL_SIMULATE": "0",
        "DB_POOL_SIZE": "3",
        "DB_MAX_OVERFLOW": "1",
        "DEVICE_OFFLINE_AFTER_S": "5",
        "PRESENCE_BATCH_SIZE": "40",
        "PRESENCE_FLUSH_INTERVAL_S": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    # No real notification credentials or recipient records in this test DB.
    for name in list(env):
        if name.startswith("SMTP_"):
            env.pop(name)
    capture = NotificationCapture()
    env.update(SMTP_HOST="127.0.0.1", SMTP_PORT=str(capture.smtp.server_address[1]),
               SMTP_STARTTLS="0", SMTP_USERNAME="", SMTP_PASSWORD="",
               SMTP_FROM="validation@example.invalid", OMP_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMBA_NUM_THREADS="1")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend / "packages/db/alembic.ini"),
            "upgrade",
            "head",
        ],
        env=env,
        check=True,
    )
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    port, ui_port = free_port(), free_port()
    url = f"http://127.0.0.1:{port}"
    processes, logs = {}, []
    db_probe = None
    sampler = None
    affinity = (
        [int(value) for value in args.cpu_affinity.split(",")]
        if args.cpu_affinity
        else None
    )

    def launch(name, command, **kwargs):
        logfile = (output / f"{name}.log").open("w", encoding="utf-8")
        logs.append(logfile)
        process = subprocess.Popen(
            command,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            env=kwargs.pop("env", env),
            **kwargs,
        )
        processes[name] = process
        monitor = psutil.Process(process.pid)
        if affinity:
            monitor.cpu_affinity(affinity)
        return process

    def request(path, data=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        with urlopen(
            Request(
                url + path,
                json.dumps(data).encode() if data is not None else None,
                headers,
            ),
            timeout=4,
        ) as response:
            return json.load(response)

    ws_state = {
        "live_frames": 0,
        "presence_seen": False,
        "fall_state_seen": False,
        "error": None,
    }
    stop_ws = threading.Event()
    wall_started = time.monotonic()
    try:
        launch(
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "wifiguard_api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--limit-concurrency", "24",
                "--no-proxy-headers",
            ],
            cwd=backend,
        )
        for _ in range(80):
            try:
                health = request("/health")
                break
            except Exception:  # noqa: BLE001 - bounded readiness retry
                if processes["api"].poll() is not None:
                    raise RuntimeError("test API stopped; see api.log")
                time.sleep(0.1)
        else:
            raise RuntimeError("test API did not start")
        signup = request(
            "/api/v1/auth/signup",
            {
                "email": f"pi5-{suffix}@example.invalid",
                "password": secrets.token_urlsafe(20),
                "name": "Pi5 validation only",
                "service": "HOME",
            },
        )
        token = signup["accessToken"]
        device = request(
            "/api/v1/devices",
            {"name": "Replay sensor", "room": "validation", "connection": "MQTT"},
            token,
        )
        resident = request(
            "/api/v1/residents",
            {
                "name": "Synthetic replay",
                "room": "validation",
                "deviceIds": [device["id"]],
            },
            token,
        )
        env["LOCAL_DEVICE_ID"], env["LOCAL_TENANT_ID"] = (
            device["id"],
            f"home-{signup['user']['id']}",
        )
        request("/api/v1/recipients", {
            "name": "Loopback validation only", "role": "FAMILY", "residentId": resident["id"],
            "email": "validation@example.invalid", "emailEnabled": True,
            "push": True, "ntfyTopic": "validation-only",
            "ntfyServer": f"http://127.0.0.1:{capture.push.server_address[1]}", "enabled": True,
        }, token)

        def subscribe():
            from websockets.sync.client import connect

            try:
                with connect(
                    f"ws://127.0.0.1:{port}/ws/live", open_timeout=5
                ) as websocket:
                    websocket.send(json.dumps({"type": "auth", "token": token}))
                    hello = json.loads(websocket.recv(timeout=5))
                    if hello.get("type") != "hello":
                        raise ValueError("WS auth failed")
                    websocket.send(json.dumps({"type": "subscribe", "device_ids": []}))
                    while not stop_ws.is_set():
                        try:
                            event = json.loads(websocket.recv(timeout=1))
                        except TimeoutError:
                            continue
                        if event.get("type") == "live":
                            ws_state["live_frames"] += 1
                            for value in event.get("devices", []):
                                ws_state["presence_seen"] |= bool(value.get("presence"))
                                ws_state["fall_state_seen"] |= bool(value.get("fall"))
            except Exception as exc:  # noqa: BLE001 - thread error fails the test gate
                ws_state["error"] = str(exc)

        ws_thread = threading.Thread(target=subscribe, daemon=True)
        ws_thread.start()
        launch(
            "ui",
            [args.node, "--max-old-space-size=192", "scripts/serve-pi5.mjs"],
            cwd=frontend,
            env={**env, "UI_PORT": str(ui_port)},
        )
        traffic_url = f"http://127.0.0.1:{ui_port}"
        api_traffic_url = url
        if args.nginx:
            proxy_port = free_port()
            nginx_config = output / "nginx.conf"
            nginx_config.write_text(
                "worker_processes 1;\n"
                f"pid {output.as_posix()}/nginx.pid;\nerror_log stderr warn;\n"
                "events { worker_connections 64; }\nhttp { include /etc/nginx/mime.types; access_log off; server {\n"
                f"listen 127.0.0.1:{proxy_port};\n"
                f"location /api/ {{ proxy_pass http://127.0.0.1:{port}; }}\n"
                f"location /health {{ proxy_pass http://127.0.0.1:{port}; }}\n"
                "location /internal/ { return 404; }\n"
                f"location / {{ proxy_pass http://127.0.0.1:{ui_port}; }}\n"
                "} }\n", encoding="utf-8",
            )
            launch("nginx", [args.nginx, "-c", str(nginx_config), "-g", "daemon off;"])
            traffic_url = f"http://127.0.0.1:{proxy_port}"
            api_traffic_url = traffic_url
        for _ in range(100):
            try:
                with urlopen(traffic_url + "/login", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("built UI/proxy did not start")
        edge_command = build_edge_command(args, url, output)
        launch("edge", edge_command)
        if args.database_container:
            db_probe = DockerDatabaseProbe(args.database_container)
            for _ in range(30):
                if db_probe.snapshot().get("cpu_seconds") is not None:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("DB resource probe unavailable")
        sampler = StackSampler({k: p.pid for k, p in processes.items()}, affinity=affinity,
                               database_probe=db_probe, database_pid=args.database_pid)
        api_times, ui_times = [], []
        ui_asset_checked = False
        next_request = 0.0
        injected = False
        measurement_started = time.monotonic()
        def inject_routing_check():
            # This tests alerts/routing/dedup, not a learned fall decision.
            from wifiguard_contracts import topics
            from wifiguard_contracts.kafka import InferenceResult
            now = datetime.now(UTC)
            result = InferenceResult(
                device_id=device["id"], tenant_id=env["LOCAL_TENANT_ID"], ts=now,
                inferred_at=now, seq=10**9, proba_fall=0.99, threshold=0.5,
                postprocess="segmentation_b", decision=True, model_version="synthetic-routing-check",
            )
            event = {"topic": topics.KAFKA_INFERENCE_RESULT, "record": result.model_dump(mode="json")}
            request("/internal/edge/record", event, env["LOCAL_EDGE_TOKEN"])
            request("/internal/edge/record", event, env["LOCAL_EDGE_TOKEN"])
        while processes["edge"].poll() is None:
            sampler.sample()
            if not injected and time.monotonic() - measurement_started >= 30:
                inject_routing_check()
                injected = True
            if time.monotonic() >= next_request:
                next_request = time.monotonic() + 1
                started = time.perf_counter()
                with urlopen(Request(api_traffic_url + "/api/v1/residents",
                                     headers={"Authorization": "Bearer " + token}), timeout=4) as response:
                    json.load(response)
                api_times.append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                with urlopen(
                    traffic_url + "/login", timeout=4
                ) as response:
                    html = response.read()
                    if response.status != 200 or b"<html" not in html:
                        raise RuntimeError("built UI unavailable")
                if args.nginx and not ui_asset_checked:
                    assets = re.findall(rb'(?:src|href)="(/assets/[^"?#]+\.(?:js|css))', html)
                    if not assets:
                        raise RuntimeError("built UI exposes no JS/CSS assets")
                    for asset in set(assets):
                        with urlopen(traffic_url + asset.decode(), timeout=4) as response:
                            if response.status != 200 or not response.read(1):
                                raise RuntimeError("built UI asset unavailable")
                    ui_asset_checked = True
                ui_times.append((time.perf_counter() - started) * 1000)
            time.sleep(0.2)
        time.sleep(1.5)  # allow last presence batch to commit
        edge = json.loads((output / "edge/summary.json").read_text(encoding="utf-8"))
        health = request("/health")
        if not injected:
            raise RuntimeError("edge exited before the measured notification-routing check")
        with psycopg.connect(dsn) as connection:
            falls = connection.execute(
                "SELECT COUNT(*) FROM fall_events WHERE model_version='synthetic-routing-check'"
            ).fetchone()[0]
            db_bytes = connection.execute(
                "SELECT pg_database_size(current_database())"
            ).fetchone()[0]
        with psycopg.connect(ts_dsn) as connection:
            presence_rows = connection.execute(
                "SELECT COUNT(*) FROM presence_samples"
            ).fetchone()[0]
            ts_bytes = connection.execute(
                "SELECT pg_database_size(current_database())"
            ).fetchone()[0]
        elapsed = time.monotonic() - wall_started
        host = host_info()
        physical_pi5 = host["pi5_hardware"] and host["machine"] == "aarch64"
        summary = {
            "scope": "RAW replay + real FP32 features/inference/AB + presence/durable relay + authenticated API/WS + isolated PostgreSQL/Timescale + built UI + original notification adapters to loopback; "
            + ("physical Pi5 CPU, NOT physical ESP32/SPI/GPIO" if physical_pi5 else "NOT physical Pi5"),
            "host": host,
            "affinity": affinity,
            "model_stride_seconds": args.model_stride_seconds,
            "duration_seconds": elapsed,
            "edge": edge,
            "backend_health": health,
            "websocket": ws_state,
            "api_read_ms": distribution(api_times),
            "ui_ssr_ms": distribution(ui_times),
            "ui_assets_checked_through_nginx": ui_asset_checked,
            "whole_stack_resources": sampler.summary(),
            "resource_scope": {
                "database_same_cpu_as_apps": args.database_pid is not None,
                "database_vm_limits": db_probe.limits if db_probe else None,
                "nginx_included": bool(args.nginx),
                "physical_pi5_verified": physical_pi5,
                "physical_sensor_transport_verified": False,
                "one_windows_ecore_includes_database": False,
                "synthetic_raw_generation_included": args.replay_source == "synthetic-wgsp",
            },
            "notification": capture.status(),
            "database": {
                "presence_rows": presence_rows,
                "synthetic_falls_written": falls,
                "relational_bytes": db_bytes,
                "timescale_bytes": ts_bytes,
                "test_database_names": [database, timescale],
            },
            "real_alerts_sent": False,
            "actual_fall_accuracy_verified": False,
        }
        summary["combined_function_gate_passed"] = bool(
            edge.get("combined_local_gate_passed")
            and health["status"] == "ok"
            and presence_rows > 0
            and falls == 1
            and ws_state["presence_seen"]
            and ws_state["fall_state_seen"]
            and ws_state["error"] is None
            and api_times
            and ui_times
            and processes["edge"].returncode == 0
            and health["ingest"]["consumer"]["errors"] == 0
            and health["ingest"]["presence_sink"]["errors"] == 0
            and health["ingest"]["presence_sink"]["dropped"] == 0
            and health["ingest"]["fall_sink"]["errors"] == 0
            and capture.status()["email_captured"] >= 1
            and capture.status()["push_http_captured"] >= 1
        )
        summary["whole_stack_resource_gate_passed"] = sampler.summary()["complete"]
        atomic_json(output / "summary.json", summary)
        print(
            json.dumps(
                {
                    "output": str(output / "summary.json"),
                    "passed": summary["combined_function_gate_passed"] and summary["whole_stack_resource_gate_passed"],
                    "model_p95_ms": edge["processing_ms"]["p95"],
                    "presence_p95_ms": edge["pi5_services"]["presence_metrics"][
                        "processing_ms"
                    ]["p95"],
                }
            ),
            flush=True,
        )
        return 0 if summary["combined_function_gate_passed"] and summary["whole_stack_resource_gate_passed"] else 2
    finally:
        stop_ws.set()
        stop_owned_processes(processes, sampler)
        for process in processes.values():
            process.wait(timeout=2)
        for logfile in logs:
            logfile.close()
        if db_probe:
            db_probe.stop()
        capture.stop()
        # Test DBs remain inspectable. Caller owns their explicit cleanup.


if __name__ == "__main__":
    raise SystemExit(main())
