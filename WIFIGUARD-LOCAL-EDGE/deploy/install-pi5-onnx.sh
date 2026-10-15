#!/usr/bin/env bash
set -euo pipefail
local_edge_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(dirname -- "$local_edge_root")"
python_bin="${PYTHON_BIN:-python3}"
"$python_bin" -c 'import platform,sys; assert platform.machine()=="aarch64", "64-bit Pi OS required"; assert (3,12)<=sys.version_info[:2]<(3,14), "Pinned ONNX profile requires Python 3.12 or 3.13"'
"$python_bin" -m venv "$local_edge_root/.venv-onnx"
local_python="$local_edge_root/.venv-onnx/bin/python"
"$local_python" -m pip install --upgrade pip
"$local_python" -m pip install -r "$local_edge_root/deploy/onnx-runtime-requirements.txt" 'gpiod>=2.2.1,<3'
# Reuse source packages, but do NOT resolve their training/PyTorch dependency set.
# This profile supports only --backend onnx. CPU dependencies are explicit above.
"$local_python" -m pip install --no-deps \
  -e "$repo_root/WIFIGUARD-RASPBERRY" \
  -e "$repo_root/WIFIGUARD-BACKEND/services/csi-fall-segmentation" \
  -e "$local_edge_root"
"$local_python" -c 'import importlib.util,sys; assert importlib.util.find_spec("torch") is None; from wifiguard_local_edge.metrics import configure_cpu; configure_cpu(1,use_torch=False); from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder; CpuEdgeFeatureBuilder().warmup(); assert "torch" not in sys.modules; print("PI5_ONNX_CPU_RUNTIME_READY")'
printf '%s\n' 'Export model-fp32.onnx + model-fp32.json on the PC first. No SPI/service is started by this installer.'
