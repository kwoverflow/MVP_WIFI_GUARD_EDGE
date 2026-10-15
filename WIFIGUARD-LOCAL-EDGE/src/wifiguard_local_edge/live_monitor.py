"""Always-on aligned whole-stack cgroup monitor (no Docker/Grafana required)."""
from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil

from .control import read_json
from .metrics import atomic_json
from .stack_monitor import read_cgroup, service_group, distinct_service_cgroups

UNITS = {"edge": "wifiguard-pi5-edge.service", "api": "wifiguard-pi5-api.service",
         "ui": "wifiguard-pi5-ui.service", "nginx": "nginx.service",
         "database": "postgresql@16-main.service", "control": "wifiguard-pi5-control.service",
         "monitor": "wifiguard-pi5-monitor.service"}


class LiveStackMonitor:
    def __init__(self, directory, *, units=None, group_reader=service_group,
                 counter_reader=None, clock=time.monotonic):
        self.directory, self.units = Path(directory), dict(units or UNITS)
        self.group_reader = group_reader
        self.counter_reader = counter_reader or (lambda path: read_cgroup("/sys/fs/cgroup", path))
        self.clock, self.previous = clock, {}

    def sample(self):
        now, groups, errors, paths = self.clock(), {}, {}, {}
        cpus = psutil.cpu_count() or 1
        for name, unit in self.units.items():
            try:
                state = self.group_reader(unit)
                path = state["ControlGroup"]
                counters = self.counter_reader(path)
                previous = self.previous.get(name)
                cpu = None
                identity = (path, state["MainPID"], state["NRestarts"])
                if previous and identity == previous[0] and now > previous[1]:
                    delta = counters["cpu_usec"] - previous[2]["cpu_usec"]
                    if delta >= 0:
                        cpu = delta / (now - previous[1]) / 10000
                self.previous[name] = identity, now, counters
                paths[name] = path
                groups[name] = {"unit": unit, "cpu_percent_one_core_basis": cpu,
                                "cpu_percent_host_basis": None if cpu is None else cpu / cpus,
                                "memory_mib": counters["memory_bytes"] / 2**20,
                                "swap_mib": counters["swap_bytes"] / 2**20,
                                "io_read_bytes_total": counters["io_read_bytes"],
                                "io_write_bytes_total": counters["io_write_bytes"]}
            except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
                errors[name] = type(exc).__name__ + ": required service unavailable"
                self.previous.pop(name, None)
        if not distinct_service_cgroups(paths):
            errors["overlap"] = "service cgroups overlap; total suppressed"
        complete = not errors and all(value["cpu_percent_one_core_basis"] is not None for value in groups.values())
        total_cpu = sum(value["cpu_percent_one_core_basis"] for value in groups.values()) if complete else None
        disk = shutil.disk_usage(self.directory)
        temperature_path = Path("/sys/class/thermal/thermal_zone0/temp")
        try:
            temperature = int(temperature_path.read_text()) / 1000
        except (OSError, ValueError):
            temperature = None
        edge = read_json(self.directory / "status.json")
        stamp = edge.get("updated_at_unix")
        edge_age = max(0, time.time() - stamp) if isinstance(stamp, (int, float)) and math.isfinite(stamp) else None
        value = {"updated_at_unix": time.time(), "complete": complete, "errors": errors,
                 "scope": "edge + API + UI + PostgreSQL/Timescale + nginx + control + monitor",
                 "memory_method": "sum of distinct cgroup memory.current including charged file cache; not RSS",
                 "cpu_count": cpus, "groups": groups,
                 "total": {"cpu_percent_one_core_basis": total_cpu,
                           "cpu_percent_host_basis": None if total_cpu is None else total_cpu / cpus,
                           "memory_mib": sum(v["memory_mib"] for v in groups.values()) if complete else None,
                           "swap_mib": sum(v["swap_mib"] for v in groups.values()) if complete else None},
                 "system": {"temperature_c": temperature, "ram_available_mib": psutil.virtual_memory().available / 2**20,
                            "disk_used_gib": disk.used / 2**30, "disk_free_gib": disk.free / 2**30,
                            "disk_scope": "filesystem containing Pi5 state; not database-only size"},
                 "model_gpu_used": False, "gpu_system_utilization": None,
                 "edge_status_age_seconds": edge_age}
        atomic_json(self.directory / "stack-status.json", value)
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(os.getenv("PI5_STATE_DIR", "/var/lib/wifiguard-pi5")))
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--postgres-unit", default=os.getenv("PI5_POSTGRES_UNIT", UNITS["database"]))
    args = parser.parse_args(argv)
    if args.interval < 1:
        parser.error("monitor interval must be >= 1 second")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    monitor = LiveStackMonitor(args.output_dir, units={**UNITS, "database": args.postgres_unit})
    stop = threading.Event()
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        monitor.sample()
        stop.wait(args.interval)


if __name__ == "__main__":
    main()
