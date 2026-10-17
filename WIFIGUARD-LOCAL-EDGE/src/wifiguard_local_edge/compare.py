"""Compare original/optimized CPU computation in fresh, paced processes.

This measures the same feature/model work, not broker/container or SPI overhead.
Each worker receives the same deterministic 320 Hz CSI windows and checkpoint.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

from .metrics import (
    ResourceSampler,
    atomic_json,
    configure_cpu,
    distribution,
    host_info,
)


def reduction_percent(before, after):
    """Positive means less time/resource; negative means an increase."""
    return 100.0 * (1.0 - after / before) if before else None


def worker(args):
    import psutil

    if args.cpu_affinity:
        psutil.Process().cpu_affinity([int(i) for i in args.cpu_affinity.split(",")])
    configure_cpu(args.threads)
    import numpy as np
    import psutil
    import torch
    from threadpoolctl import threadpool_limits
    from csi_fall_segmentation.engine import SegmentationInferenceEngine
    from .benchmark import sample_window

    startup_sampler = ResourceSampler()
    startup_sampler.start()
    started = time.perf_counter()
    with threadpool_limits(limits=1, user_api="blas"):
        engine = SegmentationInferenceEngine(args.checkpoint, "cpu")
        if args.variant == "baseline":
            from csi_fall_segmentation.online import SegmentationFeatureBuilder
            from wifiguard_edge.features.realtime import extract_window_signal

            builder = SegmentationFeatureBuilder()

            def build(times, raw):
                signal = extract_window_signal(times, raw)
                return builder.build(signal.amplitude, signal.fs_hz)

        else:
            from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder
            from wifiguard_edge.features.realtime import (
                FeatureConfig,
                select_subcarrier_indices,
            )
            from .buffer import prepare_window

            builder = CpuEdgeFeatureBuilder()
            selected = select_subcarrier_indices(245, FeatureConfig())

            def build(times, raw):
                amplitude = prepare_window(times, raw[:, selected])
                return builder.build(amplitude, 320.0)

        builder.warmup()
        engine.warmup()
        # Warm both paths with actual varying input, excluding this from steady metrics.
        for seed in range(5):
            features = build(*sample_window(seed))
            engine.predict_segmentation_batch(features.s3, features.acf)
        startup_seconds = time.perf_counter() - started
        startup_resources = startup_sampler.snapshot()
        startup_sampler.stop()

        sampler = ResourceSampler()
        sampler.start()
        rows, rss_values = [], []
        deadline_misses = 0
        measurement_start = time.perf_counter()
        try:
            for index in range(args.windows):
                deadline = measurement_start + index * args.stride_seconds
                wait = deadline - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                start_lateness_ms = max(0.0, time.perf_counter() - deadline) * 1000
                # Common synthetic input generation is outside processing latency,
                # but included in paced process CPU use for both variants.
                times, raw = sample_window(index)
                begin = time.perf_counter()
                features = build(times, raw)
                featured = time.perf_counter()
                probabilities = engine.predict_segmentation_batch(
                    features.s3, features.acf
                )
                inferred = time.perf_counter()
                if not np.isfinite(probabilities).all():
                    raise RuntimeError("nonfinite model output")
                if inferred > deadline + args.stride_seconds:
                    deadline_misses += 1
                rows.append(
                    {
                        "index": index,
                        "feature_ms": (featured - begin) * 1000,
                        "inference_ms": (inferred - featured) * 1000,
                        "total_ms": (inferred - begin) * 1000,
                        "start_lateness_ms": start_lateness_ms,
                    }
                )
                rss_values.append(psutil.Process().memory_info().rss / 2**20)
            # Complete the final stride so CPU averages use an equal denominator.
            wait = (
                measurement_start
                + args.windows * args.stride_seconds
                - time.perf_counter()
            )
            if wait > 0:
                time.sleep(wait)
            resources = sampler.snapshot()
            resources["steady_rss_median_mib"] = float(np.median(rss_values))
        finally:
            sampler.stop()
    result = {
        "schema": "wifiguard-isolated-cpu-run-v1",
        "variant": args.variant,
        "host": host_info(),
        "device": str(engine.device),
        "torch_version": torch.__version__,
        "threads": args.threads,
        "cpu_affinity": psutil.Process().cpu_affinity(),
        "stride_seconds": args.stride_seconds,
        "windows": args.windows,
        "startup_seconds": startup_seconds,
        "startup_resources": startup_resources,
        "resources": resources,
        "checkpoint_sha256": engine.checkpoint_sha256,
        "deadline_misses": deadline_misses,
        "feature_ms": distribution([r["feature_ms"] for r in rows]),
        "inference_ms": distribution([r["inference_ms"] for r in rows]),
        "total_ms": distribution([r["total_ms"] for r in rows]),
        "samples": rows,
    }
    atomic_json(args.output, result)
    return result


def aggregate(runs):
    import numpy as np

    rows = [row for run in runs for row in run["samples"]]
    seconds = sum(run["resources"]["measurement_seconds"] for run in runs)
    cpu_seconds = sum(
        run["resources"]["measurement_seconds"]
        * run["resources"]["process_cpu_percent_one_core_basis"]
        / 100
        for run in runs
    )
    return {
        "runs": len(runs),
        "windows": len(rows),
        "measurement_seconds": seconds,
        "cpu_seconds": cpu_seconds,
        "cpu_percent_one_core_basis": 100 * cpu_seconds / seconds,
        "cpu_ms_per_window": 1000 * cpu_seconds / len(rows),
        "steady_rss_median_mib": float(
            np.median([run["resources"]["steady_rss_median_mib"] for run in runs])
        ),
        "steady_sampled_peak_rss_mib": max(
            run["resources"]["sampled_peak_rss_mib"] for run in runs
        ),
        "startup_sampled_peak_rss_mib": max(
            run["startup_resources"]["sampled_peak_rss_mib"] for run in runs
        ),
        "deadline_misses": sum(run["deadline_misses"] for run in runs),
        **{
            field: distribution([r[field] for r in rows])
            for field in ("feature_ms", "inference_ms", "total_ms", "start_lateness_ms")
        },
    }


def comparison(args):
    directory = args.output.parent
    runs = {"baseline": [], "optimized": []}
    order = []
    for repeat in range(args.repeats):
        variants = (
            ("baseline", "optimized") if repeat % 2 == 0 else ("optimized", "baseline")
        )
        for variant in variants:
            output = directory / f"{variant}-{repeat + 1}.json"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "wifiguard_local_edge.compare",
                    "--variant",
                    variant,
                    "--checkpoint",
                    str(args.checkpoint),
                    "--windows",
                    str(args.windows),
                    "--threads",
                    str(args.threads),
                    "--stride-seconds",
                    str(args.stride_seconds),
                    "--output",
                    str(output),
                ]
                + (["--cpu-affinity", args.cpu_affinity] if args.cpu_affinity else []),
                check=True,
            )
            run = json.loads(output.read_text(encoding="utf-8"))
            runs[variant].append(run)
            order.append(
                {"repeat": repeat + 1, "variant": variant, "file": output.name}
            )
    baseline, optimized = aggregate(runs["baseline"]), aggregate(runs["optimized"])
    hashes = {run["checkpoint_sha256"] for group in runs.values() for run in group}
    if len(hashes) != 1:
        raise RuntimeError("checkpoint changed during comparison")
    host = host_info()
    if platform.system() == "Windows":
        cpu_name = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor).Name",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        host["cpu_name"] = cpu_name
    result = {
        "schema": "wifiguard-isolated-cpu-comparison-v1",
        "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "host": host,
        "scope": "Same-host CPU feature generation and unchanged model inference only; no SPI, network, broker, Docker or MLOps overhead.",
        "measurement_input": "Deterministic synthetic 245-channel, 320 Hz CSI; not a fall-accuracy test.",
        "checkpoint_sha256": next(iter(hashes)),
        "threads": args.threads,
        "cpu_affinity": runs["baseline"][0]["cpu_affinity"],
        "stride_seconds": args.stride_seconds,
        "order": order,
        "baseline": baseline,
        "optimized": optimized,
        "improvement": {
            "feature_median_speedup": baseline["feature_ms"]["median"]
            / optimized["feature_ms"]["median"],
            "feature_median_reduction_percent": reduction_percent(
                baseline["feature_ms"]["median"], optimized["feature_ms"]["median"]
            ),
            "total_median_speedup": baseline["total_ms"]["median"]
            / optimized["total_ms"]["median"],
            "total_median_reduction_percent": reduction_percent(
                baseline["total_ms"]["median"], optimized["total_ms"]["median"]
            ),
            "total_p95_reduction_percent": reduction_percent(
                baseline["total_ms"]["p95"], optimized["total_ms"]["p95"]
            ),
            "cpu_reduction_percent": reduction_percent(
                baseline["cpu_percent_one_core_basis"],
                optimized["cpu_percent_one_core_basis"],
            ),
            "steady_rss_reduction_percent": reduction_percent(
                baseline["steady_rss_median_mib"], optimized["steady_rss_median_mib"]
            ),
        },
        "pi5_hardware_verified": False,
    }
    atomic_json(args.output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--variant", choices=("baseline", "optimized"))
    parser.add_argument("--windows", type=int, default=120)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--threads", type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument(
        "--cpu-affinity", help="Comma-separated logical CPU IDs, e.g. 0,2"
    )
    parser.add_argument(
        "--stride-seconds", type=float, choices=(0.25, 1.0), default=0.25
    )
    parser.add_argument(
        "--output", type=Path, default=Path("local-output/comparison/summary.json")
    )
    args = parser.parse_args(argv)
    if args.windows < 1 or args.repeats < 1:
        parser.error("windows and repeats must be positive")
    result = worker(args) if args.variant else comparison(args)
    # Keep orchestration logs bounded; full rows remain in each JSON file.
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("samples", "startup_resources")
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
