"""Reproducible same-architecture FP32 export and calibrated INT8 candidate.

No training, pruning, distillation, input resizing changes or threshold tuning.
The supplied recordings are split before calibration; evaluation is numerical
regression against the checkpoint, NOT independent clinical/fall validation.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
import sys

import numpy as np

from .metrics import atomic_json, configure_cpu


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def split_recordings(paths):
    calibration, evaluation = [], []
    for path in sorted(paths):
        # Recording split prevents overlapping 3-second windows crossing sets.
        bucket = int(hashlib.sha256(path.stem.encode()).hexdigest()[:8], 16) % 3
        (evaluation if bucket == 0 else calibration).append(path)
    if not calibration or not evaluation:
        raise ValueError("both recording-disjoint sets are required")
    return calibration, evaluation


def samples(paths, per_recording):
    for path in paths:
        with np.load(path, allow_pickle=False) as data:
            s3, acf = data["s3"], data["pca_acf_lag0p4s"]
            starts = data["window_start"]
            if s3.shape[1:] != (224, 224) or acf.shape[1:] != (1, 128, 64):
                raise ValueError(f"unexpected feature shape: {path}")
            labels = data["grid_labels"]
            center_labels = labels[starts + 250]
            # Include both classes where available, then evenly spaced windows.
            indices = []
            for label in (0, 1):
                candidates = np.flatnonzero(center_labels == label)
                if len(candidates):
                    indices.append(int(candidates[len(candidates) // 2]))
            indices.extend(np.linspace(0, len(s3) - 1, per_recording, dtype=int).tolist())
            indices = sorted(set(indices[:per_recording]))
            for index in indices:
                a = np.asarray(s3[index:index + 1], dtype=np.float32)
                b = np.asarray(acf[index:index + 1], dtype=np.float32)
                if not np.isfinite(a).all() or not np.isfinite(b).all():
                    raise ValueError(f"non-finite features: {path}:{index}")
                yield {"s3": a, "acf": b}, {
                    "recording": path.stem, "index": index,
                    "center_label": int(center_labels[index]),
                }


def regression(reference, candidate, labels):
    error = np.abs(reference - candidate)
    # With 64 equal bins, the exact center lies between bins 31 and 32.
    centers_ref = (reference[:, 31] + reference[:, 32]) * 0.5
    centers_candidate = (candidate[:, 31] + candidate[:, 32]) * 0.5
    agreement = (centers_ref >= 0.5) == (centers_candidate >= 0.5)
    center_errors = np.abs(centers_ref - centers_candidate)
    positive = np.asarray(labels) == 1
    recalls = [float(np.mean(c[positive] >= 0.5)) if positive.any() else None
               for c in (centers_ref, centers_candidate)]
    return {
        "windows": len(reference), "positive_center_labels": int(positive.sum()),
        "negative_center_labels": int((np.asarray(labels) == 0).sum()),
        "unknown_center_labels": int((~np.isin(labels, [0, 1])).sum()),
        "max_bin_probability_error": float(error.max()),
        "mean_bin_probability_error": float(error.mean()),
        "p99_center_probability_error": float(np.percentile(center_errors, 99)),
        "center_threshold_agreement": float(agreement.mean()),
        "center_threshold_changed_windows": int((~agreement).sum()),
        "b_evidence_threshold_changed_windows": int(np.sum((centers_ref >= 0.25) != (centers_candidate >= 0.25))),
        "bin_threshold_agreement": float(np.mean((reference >= 0.5) == (candidate >= 0.5))),
        "original_center_recall_on_sample": recalls[0],
        "candidate_center_recall_on_sample": recalls[1],
        "accuracy_claim": "provided-data numerical regression only; training overlap unknown",
    }


def gate(result, quantized):
    if not quantized:
        return (result["max_bin_probability_error"] <= 1e-4
                and result["center_threshold_changed_windows"] == 0
                and result["b_evidence_threshold_changed_windows"] == 0)
    recall_a = result["original_center_recall_on_sample"]
    recall_b = result["candidate_center_recall_on_sample"]
    return (
        result["max_bin_probability_error"] <= 0.05
        and result["p99_center_probability_error"] <= 0.02
        and result["center_threshold_agreement"] >= 0.995
        and result["b_evidence_threshold_changed_windows"] == 0
        and recall_a is not None and recall_b >= recall_a - 0.01
    )


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--feature-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--calibration-per-recording", type=int, default=4)
    parser.add_argument("--evaluation-per-recording", type=int, default=16)
    args = parser.parse_args(argv)
    if min(args.calibration_per_recording, args.evaluation_per_recording) < 2:
        parser.error("at least two windows per recording are required")
    configure_cpu(4)
    import torch
    import onnx
    import onnxruntime as ort
    from onnxruntime.quantization import (
        CalibrationDataReader, CalibrationMethod, QuantFormat, QuantType, quantize_static,
    )
    from onnxruntime.quantization.shape_inference import quant_pre_process
    from csi_fall_segmentation.engine import SegmentationInferenceEngine

    engine = SegmentationInferenceEngine(args.checkpoint, device="cpu")
    calibration_paths, evaluation_paths = split_recordings(args.feature_root.glob("*.npz"))
    calibration_data = list(samples(calibration_paths, args.calibration_per_recording))
    evaluation_data = list(samples(evaluation_paths, args.evaluation_per_recording))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    original_path = args.output_dir / "model-export.onnx"
    fp32_path = args.output_dir / "model-fp32.onnx"
    int8_path = args.output_dir / "model-int8.onnx"

    class OriginalForward(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.model = engine.model

        def forward(self, s3, acf):
            a = engine.normalization["feature_a"]
            b = engine.normalization["feature_b"]
            s3 = (s3[:, None] - float(a["mean"])) / max(float(a["std"]), 1e-6)
            acf = (acf - float(b["mean"])) / max(float(b["std"]), 1e-6)
            return torch.sigmoid(self.model(s3, acf, engine.image_size))

    print("EXPORT_BEGIN", flush=True)
    example = calibration_data[0][0]
    torch.onnx.export(
        OriginalForward().eval(), (torch.from_numpy(example["s3"]), torch.from_numpy(example["acf"])),
        str(original_path), input_names=["s3", "acf"], output_names=["probabilities"],
        opset_version=18, dynamo=True, external_data=False,
    )
    onnx.checker.check_model(str(original_path))
    quant_pre_process(str(original_path), str(fp32_path), skip_symbolic_shape=True)
    print("CALIBRATION_BEGIN", len(calibration_data), flush=True)

    class Reader(CalibrationDataReader):
        def __init__(self):
            self.iterator = iter(calibration_data)

        def get_next(self):
            return next(self.iterator, (None, None))[0]

    quantize_static(
        str(fp32_path), str(int8_path), Reader(), quant_format=QuantFormat.QDQ,
        per_channel=True, activation_type=QuantType.QInt8, weight_type=QuantType.QInt8,
        calibrate_method=CalibrationMethod.MinMax, op_types_to_quantize=["Conv", "Gemm", "MatMul"],
        extra_options={"ActivationSymmetric": True, "WeightSymmetric": True},
    )
    print("REGRESSION_BEGIN", len(evaluation_data), flush=True)
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = 4
    session_options.inter_op_num_threads = 1
    session_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    sessions = [ort.InferenceSession(str(p), session_options, providers=["CPUExecutionProvider"])
                for p in (fp32_path, int8_path)]
    reference, outputs = [], [[], []]
    for i, (inputs, _) in enumerate(evaluation_data):
        reference.append(engine.predict_segmentation_batch(inputs["s3"], inputs["acf"])[0])
        for session, output in zip(sessions, outputs):
            output.append(session.run(None, inputs)[0][0])
        if i % 50 == 0:
            print("REGRESSION_PROGRESS", i, flush=True)
    reference = np.asarray(reference)
    labels = [m["center_label"] for _, m in evaluation_data]
    base_metadata = {
        "contract": "inhouse-fixed-delay-s3-acf-v1", "window_seconds": engine.window_seconds,
        "window_samples": engine.window_samples, "threshold": engine.threshold,
        "checkpoint_sha256": engine.checkpoint_sha256, "model_version": engine.model_version,
        "input_shapes": {"s3": [1, 224, 224], "acf": [1, 1, 128, 64]},
        "output_shape": [1, 64], "architecture_changed": False, "retrained": False,
        "normalization": engine.normalization, "fixed_delay_seconds": 1.5,
        "torch_version": torch.__version__, "onnx_version": onnx.__version__,
        "onnxruntime_version": ort.__version__,
    }
    reports = {}
    for path, output, quantized in zip((fp32_path, int8_path), outputs, (False, True)):
        result = regression(reference, np.asarray(output), labels)
        passed = bool(gate(result, quantized))
        graph = onnx.load(str(path))
        metadata = {
            **base_metadata, "precision": "mixed-int8-qdq" if quantized else "fp32",
            "onnx_sha256": sha256(path), "bytes": path.stat().st_size,
            "regression": result, "regression_gate_passed": passed,
            "gate_limits": {"max_bin_error": 0.05 if quantized else 1e-4,
                            "p99_center_error": 0.02 if quantized else None,
                            "minimum_center_agreement": 0.995 if quantized else 1.0,
                            "maximum_recall_drop": 0.01 if quantized else 0.0},
            "ops": dict(Counter(n.op_type for n in graph.graph.node)),
            "int8_initializer_count": sum(t.data_type == onnx.TensorProto.INT8 for t in graph.graph.initializer),
        }
        atomic_json(path.with_suffix(".json"), metadata)
        reports[path.stem] = metadata
    # Representative fixed features for an identical-input fresh-process benchmark.
    np.savez_compressed(args.output_dir / "evaluation.npz",
                        s3=np.concatenate([d["s3"] for d, _ in evaluation_data]),
                        acf=np.concatenate([d["acf"] for d, _ in evaluation_data]))
    report = {
        "schema": "local-edge-quantization-v1", "created_unix": time.time(),
        "calibration_recordings": [p.stem for p in calibration_paths],
        "evaluation_recordings": [p.stem for p in evaluation_paths],
        "calibration_samples": [m for _, m in calibration_data],
        "evaluation_samples": [m for _, m in evaluation_data],
        "source_hashes": {p.name: sha256(p) for p in calibration_paths + evaluation_paths},
        "models": reports,
        "selected_default": "model-int8.onnx" if reports["model-int8"]["regression_gate_passed"] else "model-fp32.onnx",
        "caveat": "recording-disjoint PTQ regression; original training overlap unknown; not new fall accuracy validation",
    }
    atomic_json(args.output_dir / "export-report.json", report)
    print(json.dumps({"selected_default": report["selected_default"], "models": reports}), flush=True)
    return 0 if reports["model-fp32"]["regression_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
