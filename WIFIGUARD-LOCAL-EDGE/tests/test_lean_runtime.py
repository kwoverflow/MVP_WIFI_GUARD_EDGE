from pathlib import Path
import os
import subprocess
import sys

import numpy as np
import pytest


def test_lean_numpy_features_match_full_environment(tmp_path):
    root = Path(__file__).resolve().parents[1]
    default_python = root / ".venv-onnx" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    lean_python = Path(os.environ.get("WIFIGUARD_LEAN_PYTHON", str(default_python)))
    if not lean_python.is_file():
        pytest.skip("lean environment not installed; use deploy/install-pi5-onnx.sh")
    code = """
import sys
from wifiguard_local_edge.metrics import configure_cpu
configure_cpu(4, use_torch=False)
import numpy as np
from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder
from wifiguard_local_edge.benchmark import sample_window
from wifiguard_local_edge.buffer import prepare_window
from wifiguard_edge.features.realtime import select_subcarrier_indices, FeatureConfig
builder = CpuEdgeFeatureBuilder()
out = {}
for i in range(3):
    times,raw=sample_window(i)
    features=builder.build(prepare_window(times,raw[:,select_subcarrier_indices(245,FeatureConfig())]),320.0)
    out['s3_'+str(i)]=features.s3
    out['acf_'+str(i)]=features.acf
np.savez(sys.argv[1],**out)
print('torch_imported', 'torch' in sys.modules)
"""
    full = tmp_path / "full.npz"
    lean = tmp_path / "lean.npz"
    subprocess.run([sys.executable, "-c", code, str(full)], check=True, timeout=40)
    result = subprocess.run([str(lean_python), "-c", code, str(lean)], check=True,
                            timeout=40, text=True, capture_output=True)
    assert "torch_imported False" in result.stdout
    with np.load(full) as a, np.load(lean) as b:
        for key in a.files:
            np.testing.assert_array_equal(a[key], b[key])
