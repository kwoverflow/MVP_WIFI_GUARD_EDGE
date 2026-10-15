#!/usr/bin/env bash
# Future Pi5 installation only. Does NOT start services, edit secrets, flash or reboot.
set -euo pipefail
profile_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
edge_root="$(dirname -- "$(dirname -- "$profile_root")")"
repo_root="$(dirname -- "$edge_root")"
[[ "$repo_root" == /opt/wifiguard ]] || { printf '%s\n' 'Copy this checkout to /opt/wifiguard first.'; exit 1; }
[[ "$EUID" == 0 ]] || { printf '%s\n' 'Run with sudo on the intended Pi5 only.'; exit 1; }
[[ "$(uname -m)" == aarch64 ]] || { printf '%s\n' 'ARM64 Pi5 required.'; exit 1; }
[[ -f /etc/wifiguard/pi5.env ]] || { printf '%s\n' 'Private pi5.env is required; it will not be overwritten.'; exit 1; }
[[ "$(stat -c %a /etc/wifiguard/pi5.env)" == 600 ]] || { printf '%s\n' 'Private pi5.env must be mode 0600.'; exit 1; }
[[ "$(systemctl is-active wifiguard-pi5-edge.service || true)" != active ]] || { printf '%s\n' 'Stop the Edge service before installing.'; exit 1; }
local_python="$edge_root/.venv-onnx/bin/python"
"$local_python" "$profile_root/notification-preflight.py" --config /etc/wifiguard/pi5.env
# Additive migrations through 0005 (control + mobile outbox); same local DB.
"$local_python" - "$repo_root" <<'PY'
import os, subprocess, sys
from pathlib import Path
from dotenv import dotenv_values
root = Path(sys.argv[1])
env = {**os.environ, **{k: v for k, v in dotenv_values('/etc/wifiguard/pi5.env').items() if v is not None}}
subprocess.run([sys.executable, '-m', 'alembic', '-c', str(root / 'WIFIGUARD-BACKEND/packages/db/alembic.ini'),
                'upgrade', 'head'], cwd=root / 'WIFIGUARD-BACKEND', env=env, check=True)
PY
backup_dir="/var/backups/wifiguard-pi5-control-$(date -u +%Y%m%dT%H%M%S%N)"
install -d -m 0700 "$backup_dir"
for unit in wifiguard-pi5-api.service wifiguard-pi5-ui.service wifiguard-pi5-edge.service wifiguard-pi5-control.service wifiguard-pi5-monitor.service; do
  target="/etc/systemd/system/$unit"
  [[ ! -f "$target" ]] || cp -p -- "$target" "$backup_dir/$unit"
  install -m 0644 "$profile_root/$unit" "$target"
done
policy=/etc/polkit-1/rules.d/49-wifiguard-edge-control.rules
[[ ! -f "$policy" ]] || cp -p -- "$policy" "$backup_dir/49-wifiguard-edge-control.rules"
install -m 0644 "$profile_root/49-wifiguard-edge-control.rules" "$policy"
systemctl daemon-reload
printf '%s\n' "CONTROL_FILES_PREPARED backups=$backup_dir" \
  'No service was enabled or started. Copy the rebuilt UI separately.' \
  'Then restart API and start control/monitor explicitly; register/bind SPI in the GUI.'
