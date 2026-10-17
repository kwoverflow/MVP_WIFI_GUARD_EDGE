"""CPU-only before/after measurements and feature/probability parity gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from .metrics import (
    ResourceSampler,
    atomic_json,
    configure_cpu,
    distribution,
    host_info,
)


def sample_window(seed):
    import numpy as np

    rng = np.random.default_rng(seed)
    t = np.arange(960, dtype=np.float32) / 320.0
    channels = np.arange(245, dtype=np.float32)[None]
    burst = np.exp(-(((t - 1.5) / 0.15) ** 2))[:, None] * np.sin(
        2 * np.pi * 16 * t[:, None]
    )
    values = 20 + np.sin(2 * np.pi * (0.8 + channels / 490) * t[:, None])
    values += burst * (0.2 + channels / 490) + rng.normal(0, 0.01, values.shape)
    return t.astype(np.float64), values.astype(np.float32)


def timed(call):
    started = time.perf_counter()
    result = call()
    return result, (time.perf_counter() - started) * 1000


def benchmark(args):
    if args.cpu_affinity:
        import psutil

        psutil.Process().cpu_affinity([int(i) for i in args.cpu_affinity.split(",")])
    configure_cpu(args.threads)
    import numpy as np
    from threadpoolctl import threadpool_limits
    from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder
    from csi_fall_segmentation.engine import SegmentationInferenceEngine
    from csi_fall_segmentation.online import SegmentationFeatureBuilder
    from wifiguard_edge.features.realtime import (
        FeatureConfig,
        extract_window_signal,
        select_subcarrier_indices,
    )
    from .buffer import prepare_window

    sampler = ResourceSampler()
    sampler.start()
    try:
        with threadpool_limits(limits=1, user_api="blas"):
            startup = time.perf_counter()
            engine = SegmentationInferenceEngine(args.checkpoint, "cpu")
            reference = SegmentationFeatureBuilder()
            optimized = CpuEdgeFeatureBuilder()
            reference.warmup(batch_size=1)
            optimized.warmup()
            engine.warmup()
            startup_seconds = time.perf_counter() - startup
            baseline_ms, optimized_ms, inference_ms, total_ms = [], [], [], []
            stages = {}
            errors = {"s3": 0.0, "acf": 0.0, "probability_bins": 0.0}
            same_threshold = True
            for i in range(args.windows):
                times, raw = sample_window(i)

                # Mirrors Pi3+server path including the unused legacy Q/PCA pass.
                def old_path():
                    signal = extract_window_signal(times, raw)
                    return reference.build(signal.amplitude, signal.fs_hz)

                old, baseline_time = timed(old_path)

                def new_path():
                    selected = select_subcarrier_indices(245, FeatureConfig())
                    amplitude = prepare_window(times, raw[:, selected])
                    return optimized.build(amplitude, 320.0)

                new, optimized_time = timed(new_path)
                bins, inference_time = timed(
                    lambda: engine.predict_segmentation_batch(new.s3, new.acf)
                )
                old_bins = engine.predict_segmentation_batch(old.s3, old.acf)
                errors["s3"] = max(errors["s3"], float(np.max(np.abs(old.s3 - new.s3))))
                errors["acf"] = max(
                    errors["acf"], float(np.max(np.abs(old.acf - new.acf)))
                )
                errors["probability_bins"] = max(
                    errors["probability_bins"], float(np.max(np.abs(old_bins - bins)))
                )
                same_threshold = same_threshold and bool(
                    np.array_equal(
                        old_bins >= engine.threshold, bins >= engine.threshold
                    )
                )
                baseline_ms.append(baseline_time)
                optimized_ms.append(optimized_time)
                inference_ms.append(inference_time)
                total_ms.append(optimized_time + inference_time)
                for key, value in optimized.last_timings_ms.items():
                    stages.setdefault(key, []).append(value)
            total = distribution(total_ms)
            checks = {
                "feature_max_abs_error_le_3e_4": max(errors["s3"], errors["acf"])
                <= 3e-4,
                "probability_max_abs_error_le_3e_4": errors["probability_bins"] <= 3e-4,
                "same_model_threshold_decisions": same_threshold,
                "at_least_30_windows": args.windows >= 30,
                "p95_with_20_percent_headroom": total["p95"]
                <= args.stride_seconds * 800,
                "all_windows_within_stride": total["max"] <= args.stride_seconds * 1000,
            }
            result = {
                "schema": "wifiguard-local-edge-benchmark-v1",
                "host": host_info(),
                "device": "cpu",
                "threads": args.threads,
                "windows": args.windows,
                "stride_seconds": args.stride_seconds,
                "original_trigger_cadence": args.stride_seconds == 0.25,
                "startup_seconds": startup_seconds,
                "checkpoint_sha256": engine.checkpoint_sha256,
                "baseline_feature_ms": distribution(baseline_ms),
                "optimized_feature_ms": distribution(optimized_ms),
                "inference_ms": distribution(inference_ms),
                "total_processing_ms": total,
                "feature_speedup_median": float(
                    np.median(baseline_ms) / np.median(optimized_ms)
                ),
                "optimized_stage_ms": {k: distribution(v) for k, v in stages.items()},
                "max_abs_errors": errors,
                "checks": checks,
                "host_timing_gate_passed": all(checks.values()),
                "pi5_compute_gate_passed": host_info()["pi5_hardware"]
                and all(checks.values()),
                "pi5_spi_e2e_verified": False,
                "resources": sampler.snapshot(),
                "measurement_input": "deterministic synthetic CSI, not fall-accuracy evaluation",
            }
            if args.supplier_root:
                result["supplier_reference"] = supplier_parity(
                    args.supplier_root, engine, optimized
                )
                result["host_timing_gate_passed"] &= result["supplier_reference"][
                    "passed"
                ]
                result["pi5_compute_gate_passed"] &= result["supplier_reference"][
                    "passed"
                ]
            atomic_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["host_timing_gate_passed"] else 2
    finally:
        sampler.stop()


def supplier_parity(root, engine, builder):
    """Native supplier windows (no new resampling), independent golden arrays."""
    import numpy as np
    from csi_fall_segmentation.features import (
        DEFAULT_CONFIG,
        load_raw_csi,
        native_window_starts,
    )

    raw, fs, _ = load_raw_csi(
        root / "example_raw_csv" / "20260705_005402_RX1_S1_R1_T1_D1_csi.csv",
        DEFAULT_CONFIG,
    )
    starts = native_window_starts(len(raw), fs, 3.0, 0.25)[:2]
    # Supplier input is near 166.667 Hz. Compare original/optimized at precisely
    # its fs as well as the fixed live contract: kernel-only parity covers jitter.
    from csi_fall_segmentation.features import compute_feature_pairs

    count = round(fs * 3)
    native = np.stack([raw[start : start + count] for start in starts])
    ref_s3, ref_acf = compute_feature_pairs(native, fs, DEFAULT_CONFIG)
    with np.load(
        root / "reference" / "raw_csv_smoke_2windows" / "generated_features.npz",
        allow_pickle=False,
    ) as golden:
        reference_error = max(
            float(np.max(np.abs(ref_s3 - golden["s3"]))),
            float(np.max(np.abs(ref_acf - golden["pca_acf_lag0p4s"]))),
        )
    # Fixed 500-sample adapter path is compared to the reference implementation.
    from csi_fall_segmentation.online import SegmentationFeatureBuilder

    original = SegmentationFeatureBuilder()
    feature_error = probability_error = 0.0
    for window in native:
        before = original.build(window, fs)
        after = builder.build(window, fs)
        feature_error = max(
            feature_error,
            float(np.max(np.abs(before.s3 - after.s3))),
            float(np.max(np.abs(before.acf - after.acf))),
        )
        probability_error = max(
            probability_error,
            float(
                np.max(
                    np.abs(
                        engine.predict_segmentation_batch(before.s3, before.acf)
                        - engine.predict_segmentation_batch(after.s3, after.acf),
                    )
                )
            ),
        )
    with np.load(
        root
        / "reference"
        / "raw_csv_smoke_2windows"
        / "segmentation_probabilities.npz",
        allow_pickle=False,
    ) as golden:
        golden_probability_error = float(
            np.max(
                np.abs(
                    engine.predict_segmentation_batch(ref_s3, ref_acf)
                    - golden["probabilities"]
                )
            )
        )
    return {
        "windows": len(native),
        "native_fs_hz": fs,
        "golden_feature_max_abs_error": reference_error,
        "golden_probability_max_abs_error": golden_probability_error,
        "optimized_feature_max_abs_error": feature_error,
        "optimized_probability_max_abs_error": probability_error,
        "passed": max(
            reference_error, golden_probability_error, feature_error, probability_error
        )
        <= 3e-4,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--windows", type=int, default=60)
    parser.add_argument("--threads", type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument("--cpu-affinity", help="Comma-separated logical CPU IDs")
    parser.add_argument(
        "--stride-seconds", type=float, choices=(0.25, 1.0), default=0.25
    )
    parser.add_argument("--supplier-root", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("local-output/benchmark.json")
    )
    args = parser.parse_args(argv)
    if args.windows < 1:
        parser.error("windows must be positive")
    return benchmark(args)
