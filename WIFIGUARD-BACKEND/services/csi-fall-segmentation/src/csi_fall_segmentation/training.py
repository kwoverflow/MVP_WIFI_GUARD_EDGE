"""Bounded, reproducible training and fine-tuning entry point with MLflow logging."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from .engine import select_device
from .model import build_model


@dataclass(frozen=True)
class TrainConfig:
    mode: str
    epochs: int
    batch_size: int
    learning_rate: float
    max_recordings: int | None
    max_windows: int | None
    max_batches: int | None
    freeze_encoders: bool
    seed: int


class FeatureArchiveDataset(Dataset):
    """Load reviewed NPZ archives and derive the 64 temporal target bins."""

    def __init__(
        self,
        feature_dir: Path,
        normalization: dict[str, dict[str, float]],
        output_bins: int,
        *,
        max_recordings: int | None = None,
        max_windows: int | None = None,
    ) -> None:
        paths = sorted(feature_dir.glob("*.npz"))
        if max_recordings is not None:
            paths = paths[:max_recordings]
        if not paths:
            raise ValueError(f"no feature archives found in {feature_dir}")
        s3_parts: list[np.ndarray] = []
        acf_parts: list[np.ndarray] = []
        target_parts: list[np.ndarray] = []
        recording_parts: list[np.ndarray] = []
        remaining = max_windows
        for path in paths:
            with np.load(path, allow_pickle=False) as archive:
                required = {"s3", "pca_acf_lag0p4s", "window_start", "window_samples", "grid_labels"}
                missing = required.difference(archive.files)
                if missing:
                    raise ValueError(f"{path.name} missing keys: {sorted(missing)}")
                count = len(archive["s3"])
                if remaining is not None:
                    count = min(count, remaining)
                if count <= 0:
                    break
                starts = archive["window_start"][:count].astype(np.int64)
                window_samples = int(archive["window_samples"])
                labels = archive["grid_labels"].astype(np.float32)
                offsets = (np.arange(output_bins, dtype=np.float64) + 0.5) * (
                    window_samples / output_bins
                )
                indices = np.rint(starts[:, None] + offsets[None, :]).astype(np.int64)
                indices = np.clip(indices, 0, len(labels) - 1)
                s3_parts.append(archive["s3"][:count].astype(np.float32))
                acf_parts.append(archive["pca_acf_lag0p4s"][:count].astype(np.float32))
                target_parts.append(labels[indices])
                recording_parts.append(np.full(count, path.stem, dtype=object))
                if remaining is not None:
                    remaining -= count
                    if remaining <= 0:
                        break
        s3 = np.concatenate(s3_parts)
        acf = np.concatenate(acf_parts)
        self.targets = np.concatenate(target_parts).astype(np.float32)
        self.recordings = np.concatenate(recording_parts)
        s3_norm = normalization["feature_a"]
        acf_norm = normalization["feature_b"]
        self.s3 = (
            (s3 - float(s3_norm["mean"])) / max(float(s3_norm["std"]), 1e-6)
        )[:, None]
        self.acf = (acf - float(acf_norm["mean"])) / max(float(acf_norm["std"]), 1e-6)

    def __len__(self) -> int:
        return len(self.targets)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return (
            torch.from_numpy(self.s3[index]),
            torch.from_numpy(self.acf[index]),
            torch.from_numpy(self.targets[index]),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("train", "finetune"), required=True)
    parser.add_argument("--feature-dir", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--output-checkpoint", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--max-recordings", type=int)
    parser.add_argument("--max-windows", type=int)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--freeze-encoders", action="store_true")
    parser.add_argument("--seed", type=int, default=20260923)
    parser.add_argument("--mlflow-uri", default=os.environ.get("MLFLOW_TRACKING_URI", ""))
    parser.add_argument("--experiment", default="wifiguard-segmentation")
    parser.add_argument("--registered-model", default="wifiguard-inhouse-segmentation")
    return parser.parse_args()


def _start_mlflow(args: argparse.Namespace, config: TrainConfig):
    if not args.mlflow_uri:
        return None, None
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_uri)
    mlflow.set_experiment(args.experiment)
    run = mlflow.start_run(run_name=f"{args.mode}-{int(time.time())}")
    mlflow.log_params({**asdict(config), "device_request": args.device})
    mlflow.set_tags(
        {
            "pipeline.stage": args.mode,
            "model.family": "dual-branch-temporal-segmentation",
            "data.provenance": "InhouseSegmentationRealtime",
        }
    )
    return mlflow, run


def _register_checkpoint(mlflow_module, run, checkpoint_path: Path, name: str) -> str | None:
    if mlflow_module is None or run is None:
        return None
    mlflow_module.log_artifact(str(checkpoint_path), artifact_path="checkpoints")
    from mlflow import MlflowClient

    client = MlflowClient()
    try:
        client.create_registered_model(name)
    except Exception as exc:
        if "RESOURCE_ALREADY_EXISTS" not in str(exc) and "already exists" not in str(exc):
            raise
    source = f"{run.info.artifact_uri}/checkpoints/{checkpoint_path.name}"
    version = client.create_model_version(name=name, source=source, run_id=run.info.run_id)
    return str(version.version)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.epochs < 1 or args.batch_size < 1 or args.learning_rate <= 0:
        raise ValueError("epochs, batch size, and learning rate must be positive")
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    checkpoint = torch.load(args.base_checkpoint, map_location="cpu", weights_only=True)
    model = build_model(checkpoint)
    if args.mode == "finetune":
        model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    if args.freeze_encoders:
        for module in (model.encoder_a, model.encoder_b):
            for parameter in module.parameters():
                parameter.requires_grad = False
    device = select_device(args.device)
    model.to(device)
    dataset = FeatureArchiveDataset(
        args.feature_dir,
        checkpoint["normalization"],
        int(checkpoint["model_config"]["output_bins"]),
        max_recordings=args.max_recordings,
        max_windows=args.max_windows,
    )
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, num_workers=0)
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate)
    criterion = nn.BCEWithLogitsLoss()
    config = TrainConfig(
        mode=args.mode,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        max_recordings=args.max_recordings,
        max_windows=args.max_windows,
        max_batches=args.max_batches,
        freeze_encoders=args.freeze_encoders,
        seed=args.seed,
    )
    mlflow_module, mlflow_run = _start_mlflow(args, config)
    started = time.perf_counter()
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    losses: list[float] = []
    steps = 0
    model.train()
    try:
        for epoch in range(args.epochs):
            for s3, acf, target in loader:
                s3, acf, target = s3.to(device), acf.to(device), target.to(device)
                optimizer.zero_grad(set_to_none=True)
                logits = model(s3, acf, int(checkpoint["model_config"]["image_size"]))
                loss = criterion(logits, target)
                loss.backward()
                optimizer.step()
                value = float(loss.detach().cpu())
                losses.append(value)
                steps += 1
                if mlflow_module is not None:
                    mlflow_module.log_metric("train_loss", value, step=steps)
                if args.max_batches is not None and steps >= args.max_batches:
                    break
            if args.max_batches is not None and steps >= args.max_batches:
                break
        if not losses or not all(math.isfinite(value) for value in losses):
            raise RuntimeError("training produced no finite loss")
        output = dict(checkpoint)
        output["model_state_dict"] = {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        }
        output["training_run"] = {
            "mode": args.mode,
            "steps": steps,
            "examples": len(dataset),
            "final_loss": losses[-1],
            "base_checkpoint": str(args.base_checkpoint),
        }
        args.output_checkpoint.parent.mkdir(parents=True, exist_ok=True)
        torch.save(output, args.output_checkpoint)
        registered_version = _register_checkpoint(
            mlflow_module, mlflow_run, args.output_checkpoint, args.registered_model
        )
        elapsed = time.perf_counter() - started
        summary: dict[str, Any] = {
            "status": "passed",
            "mode": args.mode,
            "device": str(device),
            "platform": platform.platform(),
            "dataset_examples": len(dataset),
            "steps": steps,
            "initial_loss": losses[0],
            "final_loss": losses[-1],
            "elapsed_seconds": elapsed,
            "examples_per_second": (steps * args.batch_size) / max(elapsed, 1e-9),
            "trainable_parameters": sum(parameter.numel() for parameter in trainable),
            "checkpoint_bytes": args.output_checkpoint.stat().st_size,
            "peak_gpu_memory_bytes": (
                int(torch.cuda.max_memory_allocated(device)) if device.type == "cuda" else 0
            ),
            "mlflow_run_id": mlflow_run.info.run_id if mlflow_run is not None else None,
            "registered_model": args.registered_model if registered_version else None,
            "registered_model_version": registered_version,
            "config": asdict(config),
        }
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if mlflow_module is not None:
            mlflow_module.log_metrics(
                {
                    "final_loss": losses[-1],
                    "elapsed_seconds": elapsed,
                    "peak_gpu_memory_bytes": summary["peak_gpu_memory_bytes"],
                }
            )
            mlflow_module.log_artifact(str(args.summary), artifact_path="reports")
        return summary
    finally:
        if mlflow_module is not None:
            mlflow_module.end_run()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
