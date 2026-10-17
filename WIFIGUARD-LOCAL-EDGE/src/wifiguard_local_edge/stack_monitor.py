"""Read-only Linux/systemd whole-stack monitoring, including the real DB cluster.

Never opens SPI, changes affinity/units, restarts services or reads credentials.
Service cgroup memory includes charged file cache, not process RSS/PSS. Missing
components and counter resets fail completeness instead of becoming zero usage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import psutil

from .metrics import atomic_json, distribution, host_info


def read_cgroup(root, relative):
    if not relative or relative == "/":
        raise ValueError("a leaf service cgroup is required")
    root = Path(root).resolve()
    directory = (root / relative.lstrip("/")).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("cgroup escapes the configured root")
    cpu = dict(line.split() for line in (directory / "cpu.stat").read_text().splitlines())
    io = {"rbytes": 0, "wbytes": 0}
    io_file = directory / "io.stat"
    if io_file.exists():
        for line in io_file.read_text().splitlines():
            fields = dict(item.split("=", 1) for item in line.split()[1:])
            for key in io:
                io[key] += int(fields.get(key, "0"))
    return {
        "cpu_usec": int(cpu["usage_usec"]),
        "memory_bytes": int((directory / "memory.current").read_text()),
        "swap_bytes": int((directory / "memory.swap.current").read_text()),
        "io_read_bytes": io["rbytes"], "io_write_bytes": io["wbytes"],
    }


def distinct_service_cgroups(groups):
    """Reject parent/child overlaps: otherwise shared memory/CPU count twice."""
    paths = [Path(value) for value in groups.values()]
    return all(not a.is_relative_to(b) and not b.is_relative_to(a)
               for index, a in enumerate(paths) for b in paths[index + 1:])


def aligned_delta(before, after, elapsed):
    if elapsed <= 0 or before.keys() != after.keys():
        raise ValueError("invalid measurement interval/components")
    result = {}
    for name, current in after.items():
        delta = current["cpu_usec"] - before[name]["cpu_usec"]
        if delta < 0:
            raise ValueError(f"{name} cgroup counter reset; service may have restarted")
        result[name] = {**current, "cpu_percent_one_core_basis": delta / elapsed / 10000}
    return {
        "groups": result,
        "total": {
            "cpu_percent_one_core_basis": sum(v["cpu_percent_one_core_basis"] for v in result.values()),
            "memory_bytes": sum(v["memory_bytes"] for v in result.values()),
            "swap_bytes": sum(v["swap_bytes"] for v in result.values()),
        },
    }


def service_group(unit):
    output = subprocess.check_output(
        ["systemctl", "show", unit, "--property=ActiveState,ControlGroup,MainPID,NRestarts"],
        text=True, timeout=3,
    )
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if fields.get("ActiveState") != "active" or int(fields.get("MainPID", "0")) <= 0:
        raise RuntimeError(f"{unit}: no running service; use the actual PostgreSQL cluster unit, not postgresql.service")
    if not fields.get("ControlGroup"):
        raise RuntimeError(f"{unit}: no cgroup")
    return fields


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=65)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--postgres-unit", default="postgresql@16-main.service")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--edge-status", type=Path, default=Path("/var/lib/wifiguard-pi5/status.json"))
    args = parser.parse_args(argv)
    if args.duration <= 0 or args.interval < 0.2:
        parser.error("positive duration and interval >= 0.2 seconds required")
    units = {
        "edge": "wifiguard-pi5-edge.service", "api": "wifiguard-pi5-api.service",
        "ui": "wifiguard-pi5-ui.service", "nginx": "nginx.service", "database": args.postgres_unit,
    }
    states = {name: service_group(unit) for name, unit in units.items()}
    groups = {name: value["ControlGroup"] for name, value in states.items()}
    if not distinct_service_cgroups(groups):
        raise RuntimeError("overlapping service cgroups would double-count usage")
    snapshot = lambda: {name: read_cgroup("/sys/fs/cgroup", path) for name, path in groups.items()}
    previous, started = snapshot(), time.monotonic()
    before_time = started
    baseline = previous
    samples, errors = [], []
    while time.monotonic() - started < args.duration:
        time.sleep(min(args.interval, max(0, started + args.duration - time.monotonic())))
        observed = time.monotonic()
        try:
            current = snapshot()
            row = aligned_delta(previous, current, observed - before_time)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            break
        row["elapsed_seconds"] = observed - started
        row["system"] = {
            "ram_available_mib": psutil.virtual_memory().available / 2**20,
            "swap_used_mib": psutil.swap_memory().used / 2**20,
        }
        try:
            row["system"]["temperature_c"] = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        except (OSError, ValueError):
            row["system"]["temperature_c"] = None
        try:
            edge_status = json.loads(args.edge_status.read_text())
            row["edge"] = {key: edge_status.get(key) for key in (
                "source", "processing_ms", "stage_ms", "pi5_services", "counters",
            )}
            row["edge_status_age_seconds"] = time.time() - args.edge_status.stat().st_mtime
        except (OSError, ValueError):
            row["edge_status_age_seconds"] = None
        samples.append(row)
        atomic_json(args.output_dir / "status.json", {
            "scope": "whole local stack cgroups including PostgreSQL/Timescale, not edge-process-only",
            "memory_method": "memory.current incl. service-charged cache", **row,
        })
        previous, before_time = current, observed
    final_states = {name: service_group(unit) for name, unit in units.items()}
    if any(states[k] != final_states[k] for k in states):
        errors.append("a service PID, cgroup or restart counter changed during measurement")
    elapsed = before_time - started
    if samples:
        cumulative = aligned_delta(baseline, previous, elapsed)
    else:
        cumulative = {"total": {}, "groups": {}}
        errors.append("no complete sample")
    summary = {
        "host": host_info(), "duration_seconds": elapsed, "sample_count": len(samples),
        "scope": "all required local E2E edge services; no MLOps/Kafka/Docker",
        "complete": not errors, "errors": errors,
        "units": units, "cgroups": groups,
        "cpu_percent_one_core_basis": cumulative["total"].get("cpu_percent_one_core_basis") if not errors else None,
        "simultaneous_cgroup_memory_mib": distribution([r["total"]["memory_bytes"] / 2**20 for r in samples]),
        "simultaneous_cgroup_swap_mib": distribution([r["total"]["swap_bytes"] / 2**20 for r in samples]),
        "groups": {
            name: {
                "cpu_percent_one_core_basis": cumulative["groups"].get(name, {}).get("cpu_percent_one_core_basis"),
                "cgroup_memory_mib": distribution([r["groups"][name]["memory_bytes"] / 2**20 for r in samples]),
                "io_read_bytes": previous[name]["io_read_bytes"] - baseline[name]["io_read_bytes"],
                "io_write_bytes": previous[name]["io_write_bytes"] - baseline[name]["io_write_bytes"],
            } for name in units
        },
        "memory_method": "aligned sum of distinct service memory.current (includes charged file cache); not RSS or sum of unrelated peaks",
        "model_execution_provider": "CPUExecutionProvider", "model_gpu_used": False,
        "gpu_system_utilization": None,
        "excluded": ["OS outside selected cgroups", "remote browser", "ESP32 consumption", "monitor controller", "external alert/cloud servers"],
    }
    atomic_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
