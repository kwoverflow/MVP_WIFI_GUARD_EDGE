"""Aligned whole-stack process measurements, not a sum of unrelated peaks.

Linux PSS avoids counting PostgreSQL shared buffers once per backend. RSS is
also exposed, but is explicitly a shared-page-double-counting upper estimate.
The measurement/controller/browser and OS are not application processes.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

import psutil

from .metrics import distribution


def simultaneous_totals(groups):
    """Never silently substitute zero for an unavailable required component."""
    if not groups or any(v.get("memory_bytes") is None for v in groups.values()):
        return {"memory_bytes": None, "pss_bytes": None}
    pss = [v.get("pss_bytes") for v in groups.values()]
    return {
        "memory_bytes": sum(v["memory_bytes"] for v in groups.values()),
        "pss_bytes": sum(pss) if all(v is not None for v in pss) else None,
    }


class DockerDatabaseProbe:
    """Read the test DB's v2 cgroup, including all PostgreSQL workers/cache.

    A tiny read-only shell probe is included in its cgroup consumption. Docker
    VM CPU is NOT pinned to a Windows E core by a cpuset inside the VM.
    """
    SCRIPT = '''printf 'PROBE_PID %s\n' "$$"
    while :; do
      cpu=; memory=; swap=
      if [ -f /sys/fs/cgroup/cpu.stat ]; then
      while read key value rest; do [ "$key" != usage_usec ] || cpu=$value; done < /sys/fs/cgroup/cpu.stat
      read memory < /sys/fs/cgroup/memory.current
      read swap < /sys/fs/cgroup/memory.swap.current
      else
      read cpu < /sys/fs/cgroup/cpuacct/cpuacct.usage
      cpu=$((cpu / 1000))
      read memory < /sys/fs/cgroup/memory/memory.usage_in_bytes
      swap=0
      if [ -f /sys/fs/cgroup/memory/memory.memsw.usage_in_bytes ]; then
      read memsw < /sys/fs/cgroup/memory/memory.memsw.usage_in_bytes
      swap=$((memsw - memory))
      fi
      fi
      printf '%s %s %s\n' "$cpu" "$memory" "$swap"
      sleep 0.2
    done'''

    def __init__(self, name):
        self.name, self.latest, self.error, self.remote_pid = name, None, None, None
        inspect = json.loads(subprocess.check_output(
            ["docker", "inspect", name], text=True
        ))[0]
        if inspect["Config"].get("Labels", {}).get("wifiguard.validation") != "fullstack":
            raise ValueError("DB probe requires an explicitly labelled isolated fullstack test container")
        self.container_id = inspect["Id"]
        self.limits = {k: inspect["HostConfig"].get(k) for k in ("CpusetCpus", "NanoCpus")}
        self.process = subprocess.Popen(
            ["docker", "exec", name, "sh", "-c", self.SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                if line.startswith("PROBE_PID "):
                    self.remote_pid = int(line.split()[1])
                    continue
                cpu, memory, swap = map(int, line.split())
                self.latest = dict(cpu_seconds=cpu / 1e6, memory_bytes=memory,
                                   swap_bytes=swap, observed_at=time.monotonic(),
                                   pss_bytes=None, memory_method="cgroup usage/current incl. cache (v1/v2)")
            if self.process.poll() not in (None, 0):
                self.error = "test DB cgroup probe stopped"
        except Exception as exc:
            self.error = type(exc).__name__

    def snapshot(self):
        value = self.latest
        if value is None or time.monotonic() - value["observed_at"] > 1:
            return dict(memory_bytes=None, pss_bytes=None, cpu_seconds=None)
        return dict(value)

    def stop(self):
        if self.remote_pid is not None:
            subprocess.run(["docker", "exec", self.container_id, "kill", "-TERM", str(self.remote_pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False)
        self.process.terminate()
        try:
            self.process.wait(3)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.thread.join(1)


class StackSampler:
    def __init__(self, roots, *, affinity=None, database_probe=None, database_pid=None):
        self.roots = dict(roots)
        if database_pid is not None:
            self.roots["database"] = database_pid
        self.affinity = affinity
        self.database_probe = database_probe
        self.started = time.monotonic()
        self.samples, self.cpu_start, self.cpu_last = [], {}, {}
        self.errors = []
        self.end_boundary_samples_omitted = 0
        self.sample(baseline=True)

    def sample(self, baseline=False):
        groups, seen = {}, set()
        controller = psutil.Process()
        controller_family = {controller.pid, *(p.pid for p in controller.children(recursive=True))}
        for name, pid in self.roots.items():
            memory, pss, pss_available = 0, 0, True
            try:
                root = psutil.Process(pid)
                family = [root, *root.children(recursive=True)]
            except psutil.NoSuchProcess:
                family = []
            except psutil.Error as exc:
                self.errors.append(f"{name}: {type(exc).__name__}")
                family = []
            for process in family:
                try:
                    if process.pid in seen or process.pid == controller.pid:
                        continue
                    if name == "database" and (process.pid in controller_family or process.name() != "postgres"):
                        continue
                    seen.add(process.pid)
                    # DB/nginx workers may have a different service UID. In the
                    # isolated Linux fixture the cpuset already pins them; an
                    # unnecessary sched_setaffinity would need CAP_SYS_NICE.
                    if self.affinity and set(process.cpu_affinity()) != set(self.affinity):
                        process.cpu_affinity(self.affinity)
                    memory += process.memory_info().rss
                    try:
                        info = process.memory_full_info()
                        if hasattr(info, "pss"):
                            pss += info.pss
                        else:
                            pss_available = False
                    except psutil.Error:
                        pss_available = False
                    key = (name, process.pid, process.create_time())
                    cpu = sum(process.cpu_times()[:2])
                    self.cpu_start.setdefault(key, cpu if baseline else 0)
                    self.cpu_last[key] = cpu
                except psutil.NoSuchProcess:
                    continue
                except psutil.Error as exc:
                    self.errors.append(f"{name}: {type(exc).__name__}")
                    pss_available = False
            groups[name] = dict(memory_bytes=memory if family else None,
                                pss_bytes=pss if family and pss_available else None,
                                memory_method="process RSS", cpu_seconds=None)
        if self.database_probe:
            groups["database"] = self.database_probe.snapshot()
            value = groups["database"].get("cpu_seconds")
            if value is not None:
                key = ("database", "docker", self.database_probe.name)
                self.cpu_start.setdefault(key, value)
                self.cpu_last[key] = value
        row = dict(elapsed=time.monotonic() - self.started, groups=groups,
                   totals=simultaneous_totals(groups))
        if not baseline:
            # A timed edge exits between poll() and /proc/psutil lookup. This is
            # the END of the measured interval, not a missing DB or 0 MiB edge.
            # Keep the previous aligned snapshot; do not fabricate a final row.
            if (groups.get("edge", {}).get("memory_bytes") is None
                    and "edge" in self.roots and not psutil.pid_exists(self.roots["edge"])):
                self.end_boundary_samples_omitted += 1
            else:
                self.samples.append(row)
        return row

    def summary(self):
        elapsed = max(self.samples[-1]["elapsed"] if self.samples else 0, 1e-9)
        names = list(self.roots)
        if self.database_probe:
            names.append("database")
        cpu = {name: sum(max(0, value - self.cpu_start[key])
                        for key, value in self.cpu_last.items() if key[0] == name)
               / elapsed * 100 for name in names}
        rows = self.samples
        memory = [r["totals"]["memory_bytes"] / 2**20 for r in rows if r["totals"]["memory_bytes"] is not None]
        pss = [r["totals"]["pss_bytes"] / 2**20 for r in rows if r["totals"]["pss_bytes"] is not None]
        complete = bool(rows) and len(memory) == len(rows) and not self.errors
        return {
            "duration_seconds": elapsed, "sample_count": len(rows),
            "sampling_interval_seconds": 0.2,
            "end_boundary_samples_omitted": self.end_boundary_samples_omitted,
            "complete_memory_samples": len(memory), "complete": complete,
            "cpu_percent_one_core_basis": sum(cpu.values()) if complete else None,
            "simultaneous_rss_or_mixed_mib": distribution(memory),
            "simultaneous_pss_mib": distribution(pss),
            "memory_method": "mixed native process RSS + DB cgroup current (incl. cache)" if self.database_probe
            else "RSS sum over unique PIDs; use PSS sum to avoid shared-memory duplication",
            "groups": {
                name: {"cpu_percent_one_core_basis": cpu[name],
                       "rss_or_cgroup_mib": distribution([r["groups"][name]["memory_bytes"] / 2**20
                           for r in rows if r["groups"][name]["memory_bytes"] is not None]),
                       "pss_mib": distribution([r["groups"][name]["pss_bytes"] / 2**20
                           for r in rows if r["groups"][name]["pss_bytes"] is not None])}
                for name in names},
            "db_included": "database" in names,
            "errors": sorted(set(self.errors)),
            "excluded": ["OS kernel and global file cache outside app cgroups", "client browser",
                         "ESP32 physical consumption", "test controller/local notification capture servers",
                         "external email/phone/cloud delivery resources"],
        }
