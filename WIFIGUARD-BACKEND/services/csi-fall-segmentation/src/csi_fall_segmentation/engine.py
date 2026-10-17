"""Safe checkpoint loading and center-bin inference for temporal segmentation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import torch

from .model import build_model


def select_device(requested: str = "auto") -> torch.device:
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        return torch.device("cuda")
    if requested == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS requested but unavailable")
        return torch.device("mps")
    if requested == "cpu":
        return torch.device("cpu")
    if requested != "auto":
        raise ValueError(f"unsupported device {requested!r}")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def center_probabilities(probabilities: np.ndarray, window_samples: int) -> np.ndarray:
    """Interpolate the exact center probability from fixed-width temporal bins."""
    values = np.asarray(probabilities, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError(f"expected (batch, bins) probabilities, got {values.shape}")
    bins = values.shape[1]
    bin_centers = (np.arange(bins, dtype=np.float64) + 0.5) * (window_samples / bins)
    center = window_samples / 2.0
    return np.asarray(
        [np.interp(center, bin_centers, row) for row in values], dtype=np.float32
    )


class SegmentationInferenceEngine:
    """Load the reviewed package without executing arbitrary checkpoint pickles."""

    def __init__(self, checkpoint_path: Path | str, device: str = "auto") -> None:
        self.checkpoint_path = Path(checkpoint_path)
        if not self.checkpoint_path.is_file():
            raise FileNotFoundError(f"checkpoint not found: {self.checkpoint_path}")
        checkpoint = torch.load(
            self.checkpoint_path,
            map_location="cpu",
            weights_only=True,
        )
        required = {"model_config", "model_state_dict", "normalization", "fixed_delay"}
        missing = required.difference(checkpoint)
        if missing:
            raise ValueError(f"segmentation checkpoint missing keys: {sorted(missing)}")
        if checkpoint.get("task") != "binary temporal fall segmentation":
            raise ValueError(f"unexpected checkpoint task: {checkpoint.get('task')!r}")

        self.device = select_device(device)
        self.model = build_model(checkpoint)
        self.model.load_state_dict(checkpoint["model_state_dict"], strict=True)
        self.model.to(self.device).eval()
        self.normalization = checkpoint["normalization"]
        self.image_size = int(checkpoint["model_config"]["image_size"])
        self.output_bins = int(checkpoint["model_config"]["output_bins"])
        self.window_seconds = float(checkpoint["fixed_delay"]["window_seconds"])
        self.window_samples = 500
        self.threshold = float(checkpoint["fixed_delay"].get("default_threshold", 0.5))
        digest = hashlib.sha256(self.checkpoint_path.read_bytes()).hexdigest()
        self.model_version = f"inhouse-segmentation:v1:{digest[:12]}"
        self.checkpoint_sha256 = digest

    def warmup(self) -> None:
        s3 = np.zeros((1, 224, 224), dtype=np.float32)
        acf = np.zeros((1, 1, 128, 64), dtype=np.float32)
        self.predict_batch(s3, acf)

    @torch.inference_mode()
    def predict_segmentation_batch(self, s3: np.ndarray, acf: np.ndarray) -> np.ndarray:
        s3_values = np.asarray(s3, dtype=np.float32)
        acf_values = np.asarray(acf, dtype=np.float32)
        if s3_values.ndim != 3 or s3_values.shape[1:] != (224, 224):
            raise ValueError(f"unexpected S3 batch shape {s3_values.shape}")
        if acf_values.ndim != 4 or acf_values.shape[1:] != (1, 128, 64):
            raise ValueError(f"unexpected PCA-ACF batch shape {acf_values.shape}")
        if len(s3_values) != len(acf_values):
            raise ValueError("S3 and PCA-ACF batch lengths differ")
        s3_norm = self.normalization["feature_a"]
        acf_norm = self.normalization["feature_b"]
        s3_input = (
            s3_values[:, None] - float(s3_norm["mean"])
        ) / max(float(s3_norm["std"]), 1e-6)
        acf_input = (
            acf_values - float(acf_norm["mean"])
        ) / max(float(acf_norm["std"]), 1e-6)
        logits = self.model(
            torch.from_numpy(s3_input).to(self.device),
            torch.from_numpy(acf_input).to(self.device),
            self.image_size,
        )
        return torch.sigmoid(logits).cpu().numpy().astype(np.float32)

    def predict_batch(self, s3: np.ndarray, acf: np.ndarray) -> np.ndarray:
        segmentation = self.predict_segmentation_batch(s3, acf)
        return center_probabilities(segmentation, self.window_samples)

    def predict(self, s3: np.ndarray, acf: np.ndarray) -> float:
        return float(self.predict_batch(s3[None], acf[None])[0])
