import pytest

from wifiguard_local_edge import runtime


@pytest.mark.parametrize("stride", [0.25, 0.5, 0.75, 1.0])
def test_local_runtime_accepts_reviewed_stride_options(stride, monkeypatch):
    captured = []
    monkeypatch.setattr(runtime, "run", lambda args: captured.append(args) or 0)
    result = runtime.main([
        "--backend", "onnx", "--model", "test-model.onnx",
        "--transport", "replay", "--stride-seconds", str(stride),
        "--threads", "1", "--cpu-affinity", "16",
    ])
    assert result == 0
    assert captured[0].stride_seconds == stride
    assert captured[0].threads == 1
    assert captured[0].cpu_affinity == [16]


def test_onnx_pi5_defaults_to_half_second_one_model_worker(monkeypatch):
    captured = []
    monkeypatch.setattr(runtime, "run", lambda args: captured.append(args) or 0)
    runtime.main(["--backend", "onnx", "--model", "test-model.onnx"])
    assert captured[0].stride_seconds == 0.5
    assert captured[0].threads == 1
    assert captured[0].presence_stride_seconds == 0.5
    assert captured[0].presence_wander_stride_seconds == 1.0


def test_legacy_torch_defaults_remain_original(monkeypatch):
    captured = []
    monkeypatch.setattr(runtime, "run", lambda args: captured.append(args) or 0)
    runtime.main(["--checkpoint", "test-model.pt"])
    assert captured[0].stride_seconds == 0.25
    assert captured[0].threads == 2


def test_explicit_onnx_thread_override_is_preserved(monkeypatch):
    captured = []
    monkeypatch.setattr(runtime, "run", lambda args: captured.append(args) or 0)
    runtime.main(["--backend", "onnx", "--model", "test-model.onnx", "--threads", "3"])
    assert captured[0].stride_seconds == 0.5
    assert captured[0].threads == 3


@pytest.mark.parametrize("stride", [0, -0.5, 0.6])
def test_unreviewed_stride_is_rejected(stride):
    with pytest.raises(SystemExit) as error:
        runtime.main([
            "--backend", "onnx", "--model", "test-model.onnx",
            "--stride-seconds", str(stride),
        ])
    assert error.value.code == 2
