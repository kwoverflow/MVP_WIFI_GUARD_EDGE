"""Original-vs-ONNX complete supplied recordings at the requested 1s cadence."""

import argparse
import json
from pathlib import Path
import time

import numpy as np

from .export_model import regression, gate
from .metrics import atomic_json, configure_cpu


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--certify", action="store_true",
                        help="Attach the recorded sequence regression to model metadata; failed models remain blocked")
    args = parser.parse_args(argv)
    configure_cpu(4)
    from csi_fall_segmentation.engine import SegmentationInferenceEngine
    from csi_fall_segmentation.postprocess import ABTriggerState
    from .onnx_engine import OnnxSegmentationEngine
    original = SegmentationInferenceEngine(args.checkpoint, "cpu")
    candidates = {p: OnnxSegmentationEngine(args.model_dir / f"model-{p}.onnx", threads=4, allow_experimental=True)
                  for p in ("fp32", "int8")}
    if any(engine.checkpoint_sha256 != original.checkpoint_sha256 for engine in candidates.values()):
        raise ValueError("candidate model and regression checkpoint do not match")
    exported = json.loads((args.model_dir / "export-report.json").read_text(encoding="utf-8"))
    # Complete chronological sequences of all recordings with positive labels.
    # Keep recordings disjoint from calibration; every fourth 0.25s input = 1s.
    records, reference, predictions, labels = [], [], {p: [] for p in candidates}, []
    changed = {p: {"a_trigger": 0, "b_trigger": 0, "b_evidence": 0} for p in candidates}
    started = time.perf_counter()
    for name in exported["evaluation_recordings"]:
        with np.load(args.feature_root / f"{name}.npz", allow_pickle=False) as data:
            center_labels = data["grid_labels"][data["window_start"] + 250]
            if not np.any(center_labels == 1):
                continue
            states = {p: ABTriggerState() for p in ("original", *candidates)}
            s3, acf = data["s3"], data["pca_acf_lag0p4s"]
            for index in range(0, len(s3), 4):
                a = np.asarray(s3[index:index+1], np.float32)
                b = np.asarray(acf[index:index+1], np.float32)
                bins = original.predict_segmentation_batch(a, b)[0]
                reference.append(bins)
                labels.append(int(center_labels[index]))
                ref_decision = states["original"].update(float((bins[31] + bins[32]) * 0.5))
                for precision, engine in candidates.items():
                    values = engine.predict_segmentation_batch(a, b)[0]
                    predictions[precision].append(values)
                    decision = states[precision].update(float((values[31] + values[32]) * 0.5))
                    for key in changed[precision]:
                        changed[precision][key] += int(ref_decision[key] != decision[key])
            records.append(name)
            print("SEQUENCE_REGRESSION", name, len(reference), flush=True)
    if not records:
        raise ValueError("no labelled positive recording in evaluation set")
    results = {}
    for precision in candidates:
        result = regression(np.asarray(reference), np.asarray(predictions[precision]), labels)
        results[precision] = {"probabilities": result, "changed_decision_ticks": changed[precision],
                              "gate_passed": bool(gate(result, precision == "int8") and not any(changed[precision].values()))}
    output = {"stride_seconds": 1.0, "windows": len(reference), "recordings": records,
              "duration_seconds": time.perf_counter() - started, "results": results,
              "scope": "all chronological ticks in provided positive-labelled held-out recordings; unknown labels retained only for output comparison; no independent accuracy claim"}
    atomic_json(args.output, output)
    if args.certify:
        for precision, engine in candidates.items():
            metadata = dict(engine.metadata)
            metadata["sequence_regression"] = {
                "stride_seconds": output["stride_seconds"], "windows": output["windows"],
                "recordings": records, **results[precision], "scope": output["scope"],
            }
            metadata["regression_gate_passed"] = bool(
                metadata["regression_gate_passed"] and results[precision]["gate_passed"]
            )
            atomic_json(engine.model_path.with_suffix(".json"), metadata)
    print(json.dumps(output), flush=True)
    return 0 if results["fp32"]["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
