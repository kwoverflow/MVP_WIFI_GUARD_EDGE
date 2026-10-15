from __future__ import annotations

import json

from helpers import signup_home


def test_training_status_reads_artifacts(client, monkeypatch, tmp_path):
    artifacts = {
        "dataset-registry.json": {
            "status": "passed", "feature_recordings": 77, "feature_windows": 15320,
            "generalization_validated": False,
        },
        "training-smoke.json": {"status": "passed", "device": "cuda", "steps": 1, "peak_gpu_memory_bytes": 10},
        "finetune-smoke.json": {"status": "passed", "device": "cuda", "steps": 1, "peak_gpu_memory_bytes": 5},
        "finetune-mlflow.json": {
            "status": "passed", "registered_model": "wifiguard-model",
            "registered_model_version": "2", "mlflow_run_id": "run-1",
        },
    }
    for name, value in artifacts.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setenv("TRAINING_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("wifiguard_api.routers.training._mlflow_healthy", lambda: True)
    actor = signup_home(client, "training-status@test.io")
    response = client.get("/api/v1/training/status", headers=actor.h)
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] == "passed" and body["mlflowHealthy"] is True
    assert body["metrics"]["featureWindows"] == 15320
    validated = client.post("/api/v1/training/validate", headers=actor.h)
    assert validated.status_code == 200 and validated.json()["overall"] == "passed"


def test_training_status_requires_auth(client):
    assert client.get("/api/v1/training/status").status_code == 401
