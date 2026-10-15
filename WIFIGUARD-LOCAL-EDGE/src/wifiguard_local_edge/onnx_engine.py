"""Same fixed-delay model contract, CPU ONNX execution without importing torch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


class OnnxSegmentationEngine:
    def __init__(self, model_path, *, threads=4, allow_experimental=False):
        import onnxruntime as ort

        self.model_path = Path(model_path)
        metadata = json.loads(self.model_path.with_suffix(".json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        if digest != metadata["onnx_sha256"]:
            raise ValueError("ONNX model checksum mismatch")
        if metadata["contract"] != "inhouse-fixed-delay-s3-acf-v1":
            raise ValueError("unreviewed ONNX input contract")
        if not metadata["regression_gate_passed"] and not allow_experimental:
            raise ValueError("model regression gate failed; use FP32 or explicitly opt in")
        self.metadata = metadata
        self.window_seconds = metadata["window_seconds"]
        self.window_samples = metadata["window_samples"]
        self.threshold = metadata["threshold"]
        self.model_version = metadata["model_version"]
        self.checkpoint_sha256 = metadata["checkpoint_sha256"]
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        ort.disable_telemetry_events()
        self.session = ort.InferenceSession(
            str(self.model_path), options, providers=["CPUExecutionProvider"]
        )
        if self.session.get_providers() != ["CPUExecutionProvider"]:
            raise RuntimeError("CPU-only execution required")

    def predict_segmentation_batch(self, s3, acf):
        # Export has fixed batch=1, matching online edge processing.
        s3 = np.asarray(s3, dtype=np.float32)
        acf = np.asarray(acf, dtype=np.float32)
        if s3.shape != (1, 224, 224) or acf.shape != (1, 1, 128, 64):
            raise ValueError("expected S3 (1,224,224), ACF (1,1,128,64)")
        if not np.isfinite(s3).all() or not np.isfinite(acf).all():
            raise ValueError("non-finite model input")
        probabilities = self.session.run(None, {"s3": s3, "acf": acf})[0]
        if probabilities.shape != (1, 64) or not np.isfinite(probabilities).all():
            raise ValueError("invalid segmentation probabilities")
        return probabilities

    def predict_batch(self, s3, acf):
        values = self.predict_segmentation_batch(s3, acf)
        # Same np.interp expression as the original engine, without torch import.
        bin_centers = (np.arange(64, dtype=np.float64) + 0.5) * (self.window_samples / 64)
        return np.asarray(
            [np.interp(self.window_samples / 2.0, bin_centers, row) for row in values],
            dtype=np.float32,
        )

    def predict(self, s3, acf):
        return float(self.predict_batch(s3[None], acf[None])[0])

    def warmup(self):
        self.predict_batch(np.zeros((1, 224, 224), np.float32), np.zeros((1, 1, 128, 64), np.float32))
