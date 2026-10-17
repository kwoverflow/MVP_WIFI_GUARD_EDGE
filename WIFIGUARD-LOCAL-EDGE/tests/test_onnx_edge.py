import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from wifiguard_local_edge.export_model import gate, regression, split_recordings
from wifiguard_local_edge.onnx_engine import OnnxSegmentationEngine


def toy_model(tmp_path, passed=True):
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import helper, TensorProto
    output = np.linspace(0, 1, 64, dtype=np.float32)[None]
    graph = helper.make_graph([
        helper.make_node("Constant", [], ["probabilities"], value=onnx.numpy_helper.from_array(output))
    ], "contract-test", [helper.make_tensor_value_info("s3", TensorProto.FLOAT, [1, 224, 224]),
                         helper.make_tensor_value_info("acf", TensorProto.FLOAT, [1, 1, 128, 64])],
        [helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, [1, 64])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=10)
    path = tmp_path / "toy.onnx"
    onnx.save(model, path)
    metadata = {"contract": "inhouse-fixed-delay-s3-acf-v1", "regression_gate_passed": passed,
                "onnx_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "window_seconds": 3.0, "window_samples": 500, "threshold": 0.5,
                "model_version": "test", "checkpoint_sha256": "test"}
    path.with_suffix(".json").write_text(json.dumps(metadata))
    return path


def test_onnx_contract_center_and_shapes(tmp_path):
    engine = OnnxSegmentationEngine(toy_model(tmp_path), threads=1)
    assert engine.predict(np.zeros((224, 224), np.float32), np.zeros((1, 128, 64), np.float32)) == pytest.approx(0.5)
    with pytest.raises(ValueError, match="expected S3"):
        engine.predict_segmentation_batch(np.zeros((2, 224, 224)), np.zeros((2, 1, 128, 64)))
    with pytest.raises(ValueError, match="non-finite"):
        engine.predict_segmentation_batch(np.full((1, 224, 224), np.nan), np.zeros((1, 1, 128, 64)))


def test_failed_quantization_is_not_silently_used(tmp_path):
    path = toy_model(tmp_path, passed=False)
    with pytest.raises(ValueError, match="regression gate failed"):
        OnnxSegmentationEngine(path)
    OnnxSegmentationEngine(path, threads=1, allow_experimental=True)
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="checksum mismatch"):
        OnnxSegmentationEngine(path, allow_experimental=True)


def test_recording_split_has_no_overlap():
    paths = [Path(f"record-{i}.npz") for i in range(77)]
    a, b = split_recordings(paths)
    assert not set(a) & set(b)
    assert set(a) | set(b) == set(paths)
    assert split_recordings(paths[::-1]) == (a, b)


def test_regression_gate_rejects_changed_decisions():
    original = np.full((4, 64), 0.49, np.float32)
    good = regression(original, original, [0, 0, 1, 1])
    bad = regression(original, original + 0.03, [0, 0, 1, 1])
    assert gate(good, False) and gate(good, True)
    assert not gate(bad, False) and not gate(bad, True)


def test_b_evidence_change_is_also_rejected():
    original = np.full((4, 64), 0.24999, np.float32)
    candidate = original + 0.00002
    result = regression(original, candidate, [0, 0, 1, 1])
    assert result["center_threshold_changed_windows"] == 0
    assert result["b_evidence_threshold_changed_windows"] == 4
    assert not gate(result, False) and not gate(result, True)


def test_onnx_feature_import_does_not_load_torch():
    code = "from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder; from wifiguard_local_edge.onnx_engine import OnnxSegmentationEngine; import sys; assert 'torch' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object cap")
def test_windows_memory_cap_is_enforced():
    code = """
from wifiguard_local_edge.budget import apply_budget
import ctypes
result = apply_budget(memory_limit_mib=128)
assert result['memory_limit_verified']
try:
    ctypes.create_string_buffer(256 * 2**20)
except MemoryError:
    print('MEMORY_CAP_ENFORCED')
else:
    raise AssertionError('memory limit was not enforced')
"""
    output = subprocess.run([sys.executable, "-c", code], check=True, timeout=30, capture_output=True, text=True)
    assert "MEMORY_CAP_ENFORCED" in output.stdout
