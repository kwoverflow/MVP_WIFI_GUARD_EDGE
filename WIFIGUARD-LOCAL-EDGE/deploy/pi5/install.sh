#!/usr/bin/env bash
set -euo pipefail
profile_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
edge_root="$(dirname -- "$(dirname -- "$profile_root")")"
repo_root="$(dirname -- "$edge_root")"
# Installs packages only; never flashes hardware, reboots, enables or starts services.
python_bin="${PYTHON_BIN:-python3}"
"$python_bin" -c 'import platform,sys; assert platform.machine()=="aarch64", "64-bit Pi OS required"; assert sys.version_info[:2]==(3,12), "This ARM64 dependency lock requires Python 3.12"'
"$python_bin" -m venv "$edge_root/.venv-onnx"
local_python="$edge_root/.venv-onnx/bin/python"
index_args=()
if [[ -d "$profile_root/wheelhouse" ]]; then
  index_args=(--no-index --find-links "$profile_root/wheelhouse")
fi
"$local_python" -m pip install "${index_args[@]}" --require-hashes --only-binary=:all: -r "$profile_root/requirements-arm64.lock"
# Exact ARM64/Python 3.12 GPIO wheel; no GPIO is opened during installation.
"$local_python" -m pip install "${index_args[@]}" --require-hashes --only-binary=:all: -r "$profile_root/requirements-gpio.lock"
"$local_python" -m pip install "${index_args[@]}" --no-deps --no-build-isolation \
  -e "$repo_root/WIFIGUARD-RASPBERRY" \
  -e "$repo_root/WIFIGUARD-BACKEND/services/csi-fall-segmentation" \
  -e "$edge_root" \
  -e "$repo_root/WIFIGUARD-BACKEND/packages/contracts" \
  -e "$repo_root/WIFIGUARD-BACKEND/packages/db" \
  -e "$repo_root/WIFIGUARD-BACKEND/services/notification" \
  -e "$repo_root/WIFIGUARD-BACKEND/services/ingest" \
  -e "$repo_root/WIFIGUARD-BACKEND/services/api"
"$local_python" -c 'import importlib.util,sys; assert importlib.util.find_spec("torch") is None; from wifiguard_local_edge.metrics import configure_cpu; configure_cpu(1,use_torch=False); from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder; CpuEdgeFeatureBuilder().warmup(); import wifiguard_local_edge.pi5, wifiguard_api.app; from google.auth.transport.requests import Request; from google.oauth2.service_account import Credentials; import dotenv; assert "torch" not in sys.modules; print("PI5_PR_NATIVE_PACKAGES_READY_WITH_FCM")'
printf '%s\n' 'Next: provision isolated PostgreSQL databases + Timescale extension, FP32 model, secrets and built UI. See README.md. Nothing was started.'
