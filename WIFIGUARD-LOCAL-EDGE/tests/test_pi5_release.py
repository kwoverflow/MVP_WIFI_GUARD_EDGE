import hashlib
import importlib.util
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

PROFILE = Path(__file__).resolve().parents[1] / "deploy/pi5"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), PROFILE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release():
    module = load("verify-release")
    payload = {name: b"fixture" for name in module.REQUIRED}
    payload[module.MODEL] = b"inert contract fixture, never loaded as an actual model"
    payload[module.MARKER] = json.dumps({"kind": "pi5-local-ui", "api_base": "same-origin",
                                       "mock": False, "pc_simulation": False}).encode()
    payload[module.METADATA] = json.dumps({"precision": "fp32", "regression_gate_passed": True,
        "architecture_changed": False, "retrained": False, "contract": "inhouse-fixed-delay-s3-acf-v1",
        "onnx_sha256": hashlib.sha256(payload[module.MODEL]).hexdigest()}).encode()
    payload["SOFTWARE-VALIDATION.json"] = json.dumps({"kind": "pi5-software-release-validation",
        "software_checks_passed": True, "hardware_tested": False, "arm64_native_execution_tested": False}).encode()
    locks = {"requirements-arm64.lock": ("google-auth", "requests", "python-dotenv"),
             "requirements-gpio.lock": ("gpiod",)}
    for lock, packages in locks.items():
        lines = []
        for package in packages:
            memory = io.BytesIO()
            with ZipFile(memory, "w") as wheel:
                wheel.writestr(package.replace("-", "_") + "-1.0.dist-info/METADATA",
                               f"Metadata-Version: 2.1\nName: {package}\nVersion: 1.0\n")
            data = memory.getvalue()
            filename = f"WIFIGUARD-LOCAL-EDGE/deploy/pi5/wheelhouse/{package.replace('-', '_')}-1.0-py3-none-any.whl"
            payload[filename] = data
            lines.append(f"{package}==1.0 --hash=sha256:{hashlib.sha256(data).hexdigest()}")
        payload["WIFIGUARD-LOCAL-EDGE/deploy/pi5/" + lock] = "\n".join(lines).encode()
    manifest = {"kind": "pi5-full-application", "includes_private_credentials": False,
                "release_id": "contract-fixture", "files": {}}
    refresh(manifest, payload)
    return module, manifest, payload


def refresh(manifest, payload):
    manifest["files"] = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                         for name, data in payload.items()}


def test_release_profile_hashes_model_ui_and_all_dependency_wheels():
    module, manifest, payload = release()
    result = module.validate(manifest, payload.__getitem__)
    assert result["valid"] and result["dependency_wheels_verified"] == 4
    assert not result["hardware_tested"] and not result["ui_pc_simulation"]


def test_vendored_metadata_does_not_replace_top_level_wheel_metadata():
    module, manifest, payload = release()
    filename = next(name for name in payload if name.endswith('.whl'))
    old = payload[filename]
    memory = io.BytesIO()
    with ZipFile(io.BytesIO(old)) as original, ZipFile(memory, 'w') as wheel:
        for name in original.namelist():
            wheel.writestr(name, original.read(name))
        wheel.writestr('vendor/unrelated-9.dist-info/METADATA', 'Name: unrelated\nVersion: 9\n')
    payload[filename] = memory.getvalue()
    lock = 'WIFIGUARD-LOCAL-EDGE/deploy/pi5/requirements-arm64.lock'
    payload[lock] = payload[lock].replace(hashlib.sha256(old).hexdigest().encode(),
        hashlib.sha256(payload[filename]).hexdigest().encode())
    refresh(manifest, payload)
    assert module.validate(manifest, payload.__getitem__)["valid"]


@pytest.mark.parametrize("bad", [{"pc_simulation": True}, {"mock": True}, {"api_base": "localhost"}])
def test_operating_release_rejects_pc_or_mock_ui(bad):
    module, manifest, payload = release()
    marker = json.loads(payload[module.MARKER]); marker.update(bad)
    payload[module.MARKER] = json.dumps(marker).encode(); refresh(manifest, payload)
    with pytest.raises(ValueError, match="operating UI"):
        module.validate(manifest, payload.__getitem__)


def test_model_bytes_cannot_be_changed_behind_the_metadata():
    module, manifest, payload = release()
    payload[module.MODEL] += b"changed"; refresh(manifest, payload)
    with pytest.raises(ValueError, match="FP32"):
        module.validate(manifest, payload.__getitem__)


@pytest.mark.parametrize("path", ["../escape.py", "/absolute.py", "C:/key.json", "settings/.env", "state/outbox.db"])
def test_distribution_never_accepts_unsafe_paths_or_runtime_credentials(path):
    module, manifest, payload = release()
    payload[path] = b"not distributed"; refresh(manifest, payload)
    with pytest.raises(ValueError):
        module.validate(manifest, payload.__getitem__)


def test_missing_dependency_wheel_and_modified_code_fail_closed():
    module, manifest, payload = release()
    wheel = next(name for name in payload if name.endswith(".whl"))
    del payload[wheel]; refresh(manifest, payload)
    with pytest.raises(ValueError, match="Complete ARM64"):
        module.validate(manifest, payload.__getitem__)
    module, manifest, payload = release()
    payload["INSTALL-PI5.md"] += b"changed"
    with pytest.raises(ValueError, match="hash mismatch"):
        module.validate(manifest, payload.__getitem__)


def test_windows_or_x86_wheels_are_not_deployable_to_pi5():
    module, manifest, payload = release()
    wheel = next(name for name in payload if name.endswith(".whl"))
    payload[wheel.replace("py3-none-any", "cp312-cp312-win_amd64")] = payload.pop(wheel)
    refresh(manifest, payload)
    with pytest.raises(ValueError, match="Linux ARM64"):
        module.validate(manifest, payload.__getitem__)


def test_failing_checks_cannot_be_packaged_as_a_prepared_release():
    module, manifest, payload = release()
    evidence = json.loads(payload["SOFTWARE-VALIDATION.json"])
    evidence["software_checks_passed"] = False
    payload["SOFTWARE-VALIDATION.json"] = json.dumps(evidence).encode()
    refresh(manifest, payload)
    with pytest.raises(ValueError, match="Passing PC software evidence"):
        module.validate(manifest, payload.__getitem__)


def test_offline_default_config_does_not_claim_external_delivery():
    checker = load("notification-preflight")
    result = checker.check({"PC_SIMULATION_MODE": "0", "MOBILE_PUSH_ENABLED": "0"})
    assert result["configuration_valid"] and len(result["warnings"]) == 2
    assert not result["external_delivery_verified"] and not result["network_requests_performed"]
    assert not checker.check({}, require_email=True, require_push=True)["configuration_valid"]


def test_email_credentials_are_checked_but_never_exposed(capsys):
    checker = load("notification-preflight")
    config = {"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_STARTTLS": "1",
              "SMTP_USERNAME": "sender@example.invalid", "SMTP_FROM": "sender@example.invalid",
              "SMTP_PASSWORD": "private-value-for-contract-only"}
    result = checker.check(config, require_email=True)
    assert result["configuration_valid"]
    assert config["SMTP_PASSWORD"] not in json.dumps(result) + capsys.readouterr().out
    config["SMTP_PORT"] = "465"
    assert not checker.check(config)["configuration_valid"]


def test_malformed_push_key_cannot_pass_even_if_file_exists(tmp_path):
    checker = load("notification-preflight")
    key = tmp_path / "private.json"
    key.write_text(json.dumps({"type": "service_account", "project_id": "wrong-project",
                              "private_key": "never-print-this"}))
    result = checker.check({"MOBILE_PUSH_ENABLED": "1", "FCM_PROJECT_ID": "correct-project",
                            "FCM_SERVICE_ACCOUNT_FILE": str(key)}, check_permissions=False)
    assert not result["configuration_valid"]
    assert "never-print-this" not in json.dumps(result)


def test_matching_private_key_is_parsed_without_any_network_request(tmp_path, monkeypatch):
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    pytest.importorskip("google.oauth2.service_account")
    import urllib.request
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline preflight must never contact a provider")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key = tmp_path / "private.json"
    key.write_text(json.dumps({"type": "service_account", "project_id": "release-project",
        "client_email": "guard@release-project.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
        "private_key": private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()).decode()}))
    result = load("notification-preflight").check({"MOBILE_PUSH_ENABLED": "1", "FCM_PROJECT_ID": "release-project",
        "FCM_SERVICE_ACCOUNT_FILE": str(key)}, require_push=True, check_permissions=False)
    assert result["configuration_valid"] and not result["network_requests_performed"]


def test_pc_notification_override_never_passes_operating_preflight():
    checker = load("notification-preflight")
    assert not checker.check({"PC_SIMULATION_MODE": "1"})["configuration_valid"]
    assert not checker.check({"MOBILE_PC_TEST_PUSH_ENABLED": "1"})["configuration_valid"]
    assert not checker.check({"MOBILE_PUSH_ENABLED": "true"})["configuration_valid"]


def test_missing_private_configuration_fails_closed(tmp_path, capsys):
    result = load("notification-preflight").main(["--config", str(tmp_path/'missing.env')])
    assert result == 1
    assert json.loads(capsys.readouterr().out)["configuration_valid"] is False


def test_installer_has_fcm_dotenv_and_no_runtime_torch_resolution():
    requirements = (PROFILE / "backend-requirements.txt").read_text()
    assert "google-auth[requests]" in requirements and "python-dotenv" in requirements
    installer = (PROFILE / "install.sh").read_text()
    assert "--no-deps --no-build-isolation" in installer
    assert "--no-index" in installer and "requirements-gpio.lock" in installer
    prepare = (PROFILE / "prepare-controls.sh").read_text()
    assert "upgrade', 'head'" in prepare and "wifiguard-pi5-ui.service" in prepare
    assert "systemctl start" not in prepare and "systemctl enable" not in prepare
