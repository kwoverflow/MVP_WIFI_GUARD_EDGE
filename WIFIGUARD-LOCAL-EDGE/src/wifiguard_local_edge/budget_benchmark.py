"""Fresh-process original/ONNX comparison with explicit CPU and memory budgets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from .metrics import ResourceSampler, atomic_json, configure_cpu, distribution, host_info
from .budget import apply_budget, efficiency_cpu_ids


def worker(args):
    affinity = [int(n) for n in args.cpu_affinity.split(",")]
    budget = apply_budget(affinity, args.memory_limit_mib)
    sampler = ResourceSampler()
    sampler.start()
    configure_cpu(args.threads, use_torch=args.variant == "torch")
    from threadpoolctl import threadpool_limits
    import numpy as np
    from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder
    from wifiguard_edge.features.realtime import FeatureConfig, select_subcarrier_indices
    from .benchmark import sample_window
    from .buffer import prepare_window

    try:
        with threadpool_limits(limits=1, user_api="blas"):
            startup = time.perf_counter()
            if args.variant == "torch":
                from csi_fall_segmentation.engine import SegmentationInferenceEngine
                engine = SegmentationInferenceEngine(args.checkpoint, "cpu")
            else:
                from .onnx_engine import OnnxSegmentationEngine
                engine = OnnxSegmentationEngine(args.model, threads=args.threads, allow_experimental=True)
            builder = CpuEdgeFeatureBuilder()
            builder.warmup()
            engine.warmup()
            indices = select_subcarrier_indices(245, FeatureConfig())
            # Same held-out real inputs in all variants, avoiding an all-zero test.
            with np.load(args.evaluation, allow_pickle=False) as archive:
                s3 = archive["s3"][:args.windows].copy()
                acf = archive["acf"][:args.windows].copy()
            if len(s3) < args.windows:
                raise ValueError("not enough evaluation windows")
            for i in range(5):
                engine.predict_segmentation_batch(s3[i:i+1], acf[i:i+1])
            startup_seconds = time.perf_counter() - startup
            sampler.stop()
            startup_peak = sampler.rss_peak / 2**20
            sampler = ResourceSampler()
            sampler.start()
            stages = {k: [] for k in ("prepare", "features", "inference", "total")}
            started = time.perf_counter()
            missed = 0
            for i in range(args.windows):
                deadline = started + i
                time.sleep(max(0, deadline - time.perf_counter()))
                times, raw = sample_window(i)
                tick = time.perf_counter()
                amplitude = prepare_window(times, raw[:, indices])
                prepared = time.perf_counter()
                builder.build(amplitude, 320.0)
                featured = time.perf_counter()
                engine.predict_segmentation_batch(s3[i:i+1], acf[i:i+1])
                inferred = time.perf_counter()
                for key, value in zip(stages, (prepared-tick, featured-prepared, inferred-featured, inferred-tick)):
                    stages[key].append(value * 1000)
                missed += int(inferred - tick > 1.0)
            time.sleep(max(0, started + args.windows - time.perf_counter()))
            resources = sampler.snapshot()
            result = {
                "schema": "pi5-resource-budget-benchmark-v1", "variant": args.variant,
                "host": host_info(), "resource_budget": budget, "threads": args.threads,
                "stride_seconds": 1.0, "startup_seconds": startup_seconds,
                "startup_sampled_peak_rss_mib": startup_peak,
                "stage_ms": {k: distribution(v) for k, v in stages.items()},
                "resources": resources, "raw_stage_ms": stages, "deadline_misses": missed,
                "checkpoint_sha256": engine.checkpoint_sha256,
                "model_sha256": getattr(engine, "metadata", {}).get("onnx_sha256"),
                "torch_imported": "torch" in sys.modules, "gpu_used": False,
                "input_scope": "synthetic 320Hz feature extraction + held-out recorded S3/ACF inference; separate inputs",
                "host_timing_gate_passed": missed == 0 and distribution(stages["total"])["p95"] <= 800,
                "pi5_hardware_proven": False,
            }
            atomic_json(args.output, result)
            print(json.dumps({k: v for k, v in result.items() if k != "raw_stage_ms"}), flush=True)
    finally:
        sampler.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--windows", type=int, default=60)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--variants", nargs="+", choices=("torch", "fp32", "int8"),
                        default=("torch", "fp32", "int8"))
    parser.add_argument("--memory-limit-mib", type=int, default=2048)
    parser.add_argument("--cpu-affinity")
    parser.add_argument("--onnx-python", type=Path)
    parser.add_argument("--variant", choices=("torch", "fp32", "int8"))
    parser.add_argument("--model", type=Path)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.windows < 30:
        parser.error("at least 30 measured windows")
    if args.threads < 1:
        parser.error("threads must be positive")
    if args.variant:
        worker(args)
        return 0
    if args.cpu_affinity:
        ids = [int(i) for i in args.cpu_affinity.split(",")]
        topology = None
    else:
        ids, topology = efficiency_cpu_ids(args.threads)
    affinity = ",".join(map(str, ids))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for variant in dict.fromkeys(args.variants):
        output = args.output_dir / f"{variant}.json"
        python = str(args.onnx_python.resolve()) if variant != "torch" and args.onnx_python else sys.executable
        command = [python, "-m", "wifiguard_local_edge.budget_benchmark",
                   "--checkpoint", str(args.checkpoint), "--model-dir", str(args.model_dir),
                   "--output-dir", str(args.output_dir), "--variant", variant,
                   "--cpu-affinity", affinity, "--memory-limit-mib", str(args.memory_limit_mib),
                   "--threads", str(args.threads),
                   "--windows", str(args.windows), "--evaluation", str(args.model_dir / "evaluation.npz"),
                   "--output", str(output)]
        if variant != "torch":
            command += ["--model", str(args.model_dir / f"model-{variant}.onnx")]
        print("FRESH_PROCESS_BEGIN", variant, flush=True)
        subprocess.run(command, check=True)
        results[variant] = json.loads(output.read_text(encoding="utf-8"))
    atomic_json(args.output_dir / "summary.json", {
        "cpu_topology": topology, "results": results,
        "scope": f"Windows x86 CPU affinity {ids}, {args.threads} inference threads, "
                 f"{args.memory_limit_mib}MiB process committed-memory cap; not ARM emulation",
        "measurement": f"{args.windows} seconds/windows per variant, same 1-second cadence, "
                       "fresh process; excludes cold start from stage timings",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
