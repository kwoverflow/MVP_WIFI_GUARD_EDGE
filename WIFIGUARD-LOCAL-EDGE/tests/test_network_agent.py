import json
from pathlib import Path
from contextlib import nullcontext
from uuid import uuid4

import pytest

from wifiguard_local_edge.network_agent import Controller, NetworkError, NetworkManager, keyfile, split_terse, validate_credentials


class FakeNM:
    def __init__(self):
        self.current = None
        self.stopped = True
        self.fail_up = False
        self.calls = []

    def edge_stopped(self): return self.stopped
    def active_uuid(self): return self.current
    def checkpoint(self):
        self.calls.append(("checkpoint", "wlan0"))
        return "/org/freedesktop/NetworkManager/Checkpoint/1"
    def checkpoint_action(self, cp, action): self.calls.append((action, cp))
    def add(self, identity, *args): self.calls.append(("add", identity))
    def up(self, identity):
        if self.fail_up: raise NetworkError("NETWORK_COMMAND_FAILED")
        self.current = identity
        self.calls.append(("up", identity))
    def save(self, identity): self.calls.append(("save", identity))
    def delete(self, identity): self.calls.append(("delete", identity))
    def status(self):
        return {"interfaces": [{"name": "wlan0", "addresses": ["192.0.2.20"], "connected": True}],
                "ssid": "공유기:1", "autoconnect": False, "edge_stopped": self.stopped}
    def scan(self): return [{"ssid": "공유기:1", "signal": 90, "security": "wpa-psk"}]


@pytest.fixture
def setup(tmp_path):
    adapter = FakeNM()
    now = [1000.0]
    ctl = Controller(adapter, tmp_path, lambda: now[0], guard=nullcontext)
    return ctl, adapter, now, tmp_path


def connect(ctl):
    return ctl.dispatch({"action": "connect", "ssid": "공유기:1", "password": "UNIQUE-TEST-SECRET",
                         "security": "wpa-psk", "hidden": False})["transaction"]


def test_commit_persists_only_non_secret_metadata(setup):
    ctl, nm, _, path = setup
    tx = connect(ctl)
    assert tx["phase"] == "awaiting_confirmation"
    assert not any(call[0] == "save" for call in nm.calls)
    assert "UNIQUE-TEST-SECRET" not in (path / "transaction.json").read_text()
    assert "checkpoint" not in tx and "previous" not in tx
    result = ctl.dispatch({"action": "confirm", "id": tx["id"]})
    assert result["transaction"]["phase"] == "committed"
    assert [c[0] for c in nm.calls][-2:] == ["save", "CheckpointDestroy"]
    restored = Controller(nm, path, guard=nullcontext)
    assert restored.public_transaction()["phase"] == "committed"
    assert not any(call[0] == "delete" for call in nm.calls)


def test_expiry_and_restart_remove_only_candidate(setup):
    ctl, nm, now, path = setup
    tx = connect(ctl)
    now[0] += 111
    ctl.expire()
    assert ctl.transaction["phase"] == "rolled_back"
    assert ("delete", tx["id"]) in nm.calls
    ctl.transaction["phase"] = "awaiting_confirmation"
    ctl.persist()
    recovered = Controller(nm, path, guard=nullcontext)
    assert recovered.transaction["phase"] == "rolled_back"


def test_connect_failure_rolls_back_without_exposing_secret(setup):
    ctl, nm, *_ = setup
    nm.fail_up = True
    with pytest.raises(NetworkError, match="^CONNECTION_FAILED$"):
        connect(ctl)
    assert ctl.transaction["phase"] == "rolled_back"
    assert any(c[0] == "CheckpointRollback" for c in nm.calls)
    assert "SECRET" not in repr(ctl.__dict__) + repr(nm.calls)


def test_running_capture_and_pending_change_block_scan_and_connect(setup):
    ctl, nm, *_ = setup
    nm.stopped = False
    for action in (lambda: connect(ctl), lambda: ctl.dispatch({"action": "scan"})):
        with pytest.raises(NetworkError, match="STOP_EDGE_FIRST"): action()
    assert nm.calls == []
    nm.stopped = True
    tx = connect(ctl)
    with pytest.raises(NetworkError, match="CONNECTION_PENDING"): connect(ctl)
    with pytest.raises(NetworkError, match="CONNECTION_PENDING"): ctl.dispatch({"action": "scan"})
    with pytest.raises(NetworkError, match="STALE_TRANSACTION"): ctl.dispatch({"action": "confirm", "id": str(uuid4())})
    ctl.dispatch({"action": "rollback", "id": tx["id"]})
    assert ctl.transaction["phase"] == "rolled_back"


@pytest.mark.parametrize("payload", [{"action": "shell"}, {"action": "connect", "interface": "eth0"},
                                     {"action": "scan", "unit": "nginx"}, [], {"action": "status", "path": "/etc/passwd"}])
def test_broker_allowlist(setup, payload):
    with pytest.raises(NetworkError, match="INVALID_REQUEST"): setup[0].dispatch(payload)


@pytest.mark.parametrize("ssid,password,security", [("", "12345678", "wpa-psk"), ("가" * 11, "12345678", "wpa-psk"),
    ("x\n[wifi]", "12345678", "wpa-psk"), ("ssid", "bad\npassword", "wpa-psk"),
    ("ssid", "short", "wpa-psk"), ("ssid", "a" * 64, "sae"), ("ssid", "12345678", "open")])
def test_invalid_credentials(ssid, password, security):
    with pytest.raises(NetworkError): validate_credentials(ssid, password, security)


def test_keyfile_encodes_ssid_and_escapes_password():
    identity = str(uuid4())
    value = keyfile(identity, "한글:공유기", " pa\\ss;word ", "wpa-psk", True)
    assert "interface-name=wlan0" in value and "autoconnect=false" in value
    assert "hidden=true" in value and "psk=\\spa\\\\ss;word\\s" in value
    assert "한글:공유기" not in value and "ssid=237;" in value
    assert "route-metric=600" in value and "eth0" not in value


def test_nm_commands_never_contain_password(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return type("Result", (), {"returncode": 0, "stdout": ""})()
    nm = NetworkManager(tmp_path, run)
    identity = str(uuid4())
    nm.add(identity, "ssid", "VERY-PRIVATE-SECRET", "wpa-psk", False)
    assert "VERY-PRIVATE-SECRET" not in repr(calls)
    assert nm.profile_path(identity).read_text().count("VERY-PRIVATE-SECRET") == 1
    with pytest.raises(FileExistsError): nm.add(identity, "ssid", "ANOTHER-SECRET", "wpa-psk", False)


def test_terse_parser_handles_ssid_colons_and_backslashes():
    assert split_terse(r"my\:network\\name:80:WPA2") == ["my:network\\name", "80", "WPA2"]


def test_scan_rate_limit_and_status_cache(setup):
    ctl, _, now, _ = setup
    assert ctl.dispatch({"action": "scan"})["networks"]
    with pytest.raises(NetworkError, match="SCAN_RATE_LIMITED"): ctl.dispatch({"action": "scan"})
    stamp = ctl.dispatch({"action": "status"})["updated_at_unix"]
    now[0] += 3
    assert ctl.dispatch({"action": "status"})["updated_at_unix"] == stamp
    now[0] += 3
    assert ctl.dispatch({"action": "status"})["updated_at_unix"] == now[0]


def test_wifi_services_in_whole_stack_totals(monkeypatch):
    from wifiguard_local_edge.live_monitor import monitored_units
    monkeypatch.delenv("PI5_WIFI_ENABLED", raising=False)
    assert "network" not in monitored_units()
    monkeypatch.setenv("PI5_WIFI_ENABLED", "1")
    assert {"network", "network_manager", "wifi_supplicant"} <= monitored_units().keys()


def test_native_network_unit_allows_readonly_ip_netlink_status():
    unit = (Path(__file__).resolve().parents[1] / "deploy/pi5/wifiguard-pi5-network.service").read_text()
    assert "RestrictAddressFamilies=AF_UNIX AF_NETLINK" in unit
    assert "ProtectSystem=strict" in unit and "ReadWritePaths=/etc/NetworkManager/system-connections" in unit
