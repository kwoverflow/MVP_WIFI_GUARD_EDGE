#!/usr/bin/env bash
set -euo pipefail

local_edge_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(dirname -- "$local_edge_root")"
python_bin="${PYTHON_BIN:-python3}"
"$python_bin" -c 'import platform, sys; assert platform.machine() == "aarch64", "Use a 64-bit Raspberry Pi OS"; assert (3,11) <= sys.version_info[:2] < (3,14), "Python 3.11-3.13 required"'
"$python_bin" -m venv "$local_edge_root/.venv"
local_python="$local_edge_root/.venv/bin/python"
"$local_python" -m pip install --upgrade pip
# CPU wheels only. No CUDA, Kafka, MQTT, MLflow, or container runtime is needed.
"$local_python" -m pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu
"$local_python" -m pip install \
    -e "$repo_root/WIFIGUARD-RASPBERRY" \
    -e "$repo_root/WIFIGUARD-BACKEND/services/csi-fall-segmentation" \
    -e "$local_edge_root[pi,onnx]"
"$local_python" -c 'import torch; assert torch.version.cuda is None, "CPU torch wheel required"; print("LOCAL_CPU_INSTALL_COMPLETE", torch.__version__)'
printf '%s\n' 'Run the CPU benchmark before connecting ESP32. See README.md for commands.'
