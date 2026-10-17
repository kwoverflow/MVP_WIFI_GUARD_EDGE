"""Read-only MLOps status and an authenticated validation action for the UI.

GPU training remains an isolated workload.  The service API deliberately does
not execute arbitrary shell commands; it reports the signed-off artifacts and
checks whether the configured MLflow registry is reachable.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import Scope, add_log, get_db, get_scope

router = APIRouter(prefix="/training", tags=["training"])

ARTIFACT_FILES = {
    "dataset": "dataset-registry.json",
    "training": "training-smoke.json",
    "finetune": "finetune-smoke.json",
    "registry": "finetune-mlflow.json",
}


def _artifact_dir() -> Path:
    return Path(os.environ.get("TRAINING_ARTIFACTS_DIR", "artifacts/validation"))


def _read_artifact(name: str) -> dict[str, Any] | None:
    path = _artifact_dir() / ARTIFACT_FILES[name]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _mlflow_healthy() -> bool:
    uri = os.environ.get("MLFLOW_TRACKING_URI", "").rstrip("/")
    if not uri:
        return False
    try:
        with urllib.request.urlopen(f"{uri}/health", timeout=2.0) as response:
            response.read()
            return response.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def build_status() -> dict[str, Any]:
    artifacts = {name: _read_artifact(name) for name in ARTIFACT_FILES}
    dataset = artifacts["dataset"] or {}
    training = artifacts["training"] or {}
    finetune = artifacts["finetune"] or {}
    registry = artifacts["registry"] or {}
    stages = [
        {
            "id": "dataset",
            "label": "데이터셋",
            "status": "passed" if dataset.get("status") == "passed" else "missing",
            "detail": f"{dataset.get('feature_recordings', 0)} recordings · {dataset.get('feature_windows', 0)} windows",
        },
        {
            "id": "training",
            "label": "전체 학습",
            "status": "passed" if training.get("status") == "passed" else "missing",
            "detail": f"{training.get('device', '—')} · {training.get('steps', 0)} step",
        },
        {
            "id": "finetune",
            "label": "파인튜닝",
            "status": "passed" if finetune.get("status") == "passed" else "missing",
            "detail": f"{finetune.get('device', '—')} · {finetune.get('steps', 0)} step",
        },
        {
            "id": "registry",
            "label": "MLflow 등록",
            "status": "passed" if registry.get("registered_model_version") else "missing",
            "detail": f"{registry.get('registered_model', '—')} v{registry.get('registered_model_version', '—')}",
        },
    ]
    return {
        "checkedAt": datetime.now(UTC).isoformat(),
        "overall": "passed" if all(stage["status"] == "passed" for stage in stages) else "degraded",
        "mlflowHealthy": _mlflow_healthy(),
        "stages": stages,
        "metrics": {
            "featureRecordings": dataset.get("feature_recordings"),
            "featureWindows": dataset.get("feature_windows"),
            "generalizationValidated": dataset.get("generalization_validated"),
            "trainingPeakGpuBytes": training.get("peak_gpu_memory_bytes"),
            "finetunePeakGpuBytes": finetune.get("peak_gpu_memory_bytes"),
            "registeredModel": registry.get("registered_model"),
            "registeredModelVersion": registry.get("registered_model_version"),
            "mlflowRunId": registry.get("mlflow_run_id"),
        },
    }


@router.get("/status")
def training_status(_: Scope = Depends(get_scope)) -> dict[str, Any]:
    return build_status()


@router.post("/validate")
def validate_training_pipeline(
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    status = build_status()
    level = "INFO" if status["overall"] == "passed" and status["mlflowHealthy"] else "WARN"
    add_log(db, scope, level, f"MLOps 파이프라인 검증: {status['overall']}")
    db.commit()
    return status
