#!/usr/bin/env bash
# Explicit opt-in. Never change eth0, boot parameters, notification credentials or firmware.
set -euo pipefail
profile_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$EUID" == 0 && "$(uname -m)" == aarch64 && "$profile_root" == /opt/wifiguard/WIFIGUARD-LOCAL-EDGE/deploy/pi5 ]] || { echo 'Run on the intended ARM64 Pi5 in /opt/wifiguard only.'; exit 1; }
[[ "${1:-}" == --activate-wifi-gui ]] || { echo 'Usage: sudo bash prepare-network.sh --activate-wifi-gui (requires Ethernet recovery access)'; exit 1; }
[[ -r /sys/firmware/devicetree/base/model ]] && grep -aq 'Raspberry Pi 5' /sys/firmware/devicetree/base/model
[[ "$(cat /sys/class/net/eth0/carrier)" == 1 && -d /sys/class/net/wlan0 ]] || { echo 'Connected Ethernet and wlan0 required.'; exit 1; }
[[ "$(systemctl show wifiguard-pi5-edge.service -p ActiveState --value)" =~ ^(inactive|failed)$ ]] || { echo 'Stop sensor monitoring first.'; exit 1; }
private_env=/etc/wifiguard/pi5.env
[[ "$(stat -c %a "$private_env")" == 600 ]]
local_python=/opt/wifiguard/WIFIGUARD-LOCAL-EDGE/.venv-onnx/bin/python
"$local_python" "$profile_root/notification-preflight.py" --config "$private_env" --require-disabled
# Do not silently migrate an existing Wi-Fi/netplan deployment.
if grep -RqsE 'wifis:|wlan0' /etc/netplan; then
  echo 'Existing wlan0/netplan configuration found. Review it before migration; nothing changed.'; exit 1
fi
backup_dir="/var/backups/wifiguard-wifi-$(date -u +%Y%m%dT%H%M%S%N)"
install -d -m 0700 "$backup_dir"
cp -p "$private_env" "$backup_dir/pi5.env"
[[ ! -d /etc/NetworkManager ]] || cp -a /etc/NetworkManager "$backup_dir/"
cp -a /etc/netplan "$backup_dir/"
[[ ! -e /etc/nginx/sites-available/wifiguard-pi5 ]] || cp -p /etc/nginx/sites-available/wifiguard-pi5 "$backup_dir/nginx.conf"
[[ ! -e /etc/systemd/system/wifiguard-pi5-network.service ]] || cp -p /etc/systemd/system/wifiguard-pi5-network.service "$backup_dir/"
install -d -m 0755 /etc/NetworkManager/conf.d
# NetworkManager will manage wlan0 ONLY, even during initial apt installation.
printf '%s\n' '[keyfile]' 'unmanaged-devices=*,except:interface-name:wlan0' > /etc/NetworkManager/conf.d/90-wifiguard-wlan-only.conf
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends network-manager wpasupplicant
install -d -m 0700 /etc/NetworkManager/system-connections
install -m 0644 "$profile_root/wifiguard-pi5-network.service" /etc/systemd/system/
install -m 0644 "$profile_root/nginx.conf" /etc/nginx/sites-available/wifiguard-pi5
nginx -t
# Atomic edit; no Wi-Fi password or notification change in the application env.
"$local_python" - "$private_env" <<'PY'
import os, sys
from pathlib import Path
path = Path(sys.argv[1])
lines = [line for line in path.read_text().splitlines() if not line.startswith('PI5_WIFI_ENABLED=')]
temp = path.with_suffix('.wifi.tmp')
fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('\n'.join(lines) + '\nPI5_WIFI_ENABLED=1\n')
os.replace(temp, path)
PY
systemctl daemon-reload
systemctl enable --now NetworkManager.service
nmcli general reload
nmcli radio wifi on
systemctl enable --now wifiguard-pi5-network.service
systemctl reload nginx.service
systemctl restart wifiguard-pi5-api.service
if systemctl is-active --quiet wifiguard-pi5-control.service; then systemctl restart wifiguard-pi5-control.service; fi
if systemctl is-active --quiet wifiguard-pi5-monitor.service; then systemctl restart wifiguard-pi5-monitor.service; fi
echo "WIFI_GUI_READY backups=$backup_dir"
echo 'No sensor start, firmware flash, reboot or external notification was performed.'
