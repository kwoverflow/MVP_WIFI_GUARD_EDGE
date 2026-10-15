"""Small local JSON reports with process and system resource measurements."""

from __future__ import annotations

import json
import os
import platform
import threading
import time
import tempfile
from pathlib import Path

import numpy as np
import psutil


def configure_cpu(threads=2, *, use_torch=True):
    # Set before ssqueezepy/numba/torch import. The receiver keeps its own thread.
    os.environ["SSQ_GPU"] = "0"
    os.environ["SSQ_PARALLEL"] = "0"
    os.environ["NUMBA_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = str(threads)
    os.environ["OMP_WAIT_POLICY"] = "PASSIVE"
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    if not use_torch:
        return
    import torch

    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)


def host_info():
    model_path = Path("/proc/device-tree/model")
    model = (
        model_path.read_text().rstrip("\x00")
        if model_path.exists()
        else platform.platform()
    )
    return {
        "model": model,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "pi5_hardware": "Raspberry Pi 5" in model,
        "ram_total_mib": psutil.virtual_memory().total / 2**20,
    }


def distribution(values):
    if not values:
        return {"count": 0, "median": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(max(values)),
    }


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows readers can briefly hold the destination open. Use a unique
    # staging file (also safe for concurrent writers) and retry only sharing
    # violations, without ever exposing partial JSON to a reader.
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
        for attempt in range(8):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


class ResourceSampler:
    def __init__(self):
        self.process = psutil.Process()
        self.rss_peak = self.process.memory_info().rss
        self.available_min = psutil.virtual_memory().available
        self.started = time.perf_counter()
        self.cpu_start = sum(self.process.cpu_times()[:2])
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        while not self._stop.wait(0.05):
            self.rss_peak = max(self.rss_peak, self.process.memory_info().rss)
            self.available_min = min(
                self.available_min, psutil.virtual_memory().available
            )

    def snapshot(self):
        elapsed = max(time.perf_counter() - self.started, 1e-9)
        return {
            "scope": "edge-process-only; use stack_monitor/validate_stack totals for whole E2E",
            "whole_stack_included": False,
            "process_rss_mib": self.process.memory_info().rss / 2**20,
            "sampled_peak_rss_mib": self.rss_peak / 2**20,
            "system_available_min_mib": self.available_min / 2**20,
            "process_cpu_percent_one_core_basis": 100
            * (sum(self.process.cpu_times()[:2]) - self.cpu_start)
            / elapsed,
            "measurement_seconds": elapsed,
            "rss_sampling_interval_ms": 50,
        }

    def stop(self):
        self._stop.set()
        self.thread.join(1)
