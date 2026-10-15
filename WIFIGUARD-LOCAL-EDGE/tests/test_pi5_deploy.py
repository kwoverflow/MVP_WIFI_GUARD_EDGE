import importlib.util
import shlex
from pathlib import Path

import pytest


def test_private_configuration_is_random_distinct_and_never_overwrites(
    tmp_path, capsys
):
    path = Path(__file__).resolve().parents[1] / "deploy/pi5/create-env.py"
    spec = importlib.util.spec_from_file_location("pi5_private_env", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "private.env"
    module.main(["--output", str(output)])
    original = output.read_text(encoding="utf-8")
    config = dict(
        line.split("=", 1)
        for line in original.splitlines()
        if line and not line.startswith("#")
    )
    assert len(config["JWT_SECRET"]) >= 32
    assert len(config["LOCAL_EDGE_TOKEN"]) >= 32
    assert config["JWT_SECRET"] != config["LOCAL_EDGE_TOKEN"]
    assert config["JWT_SECRET"] not in capsys.readouterr().out
    assert "REPLACE_PASSWORD" not in original
    assert config["MQTT_HOST"] == config["KAFKA_BOOTSTRAP"] == ""
    assert config["MODEL_CARRIER_SELECTION"] == "q-top30"
    assert config["PRESENCE_WANDER_STRIDE_SECONDS"] == "1"
    assert config["MODEL_STRIDE_SECONDS"] == "0.5"
    assert config["MODEL_THREADS"] == "1"
    with pytest.raises(FileExistsError):
        module.main(["--output", str(output)])
    assert output.read_text(encoding="utf-8") == original


def test_pi5_service_and_private_template_share_fixed_cadence():
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    service = (deploy / "pi5/wifiguard-pi5-edge.service").read_text(encoding="utf-8")
    environment = dict(
        line.removeprefix("Environment=").split("=", 1)
        for line in service.splitlines() if line.startswith("Environment=")
    )
    template = (deploy / "pi5/pi5.env.example").read_text(encoding="utf-8")
    for line in template.splitlines():
        if line and not line.startswith("#"):
            key, setting = line.split("=", 1)
            environment[key] = setting
    command = next(line.removeprefix("ExecStart=") for line in service.splitlines()
                   if line.startswith("ExecStart="))
    for key, setting in environment.items():
        command = command.replace("${" + key + "}", setting)
    arguments = shlex.split(command)
    assert "${" not in command
    for flag, expected in (
        ("--stride-seconds", "0.5"), ("--threads", "1"),
        ("--presence-stride-seconds", "0.5"),
        ("--presence-wander-stride-seconds", "1"),
        ("--model-carrier-selection", "q-top30"),
        ("--startup-calibration", "empty-room"),
    ):
        assert arguments[arguments.index(flag) + 1] == expected
    assert environment["MODEL_STRIDE_SECONDS"] == "0.5"


def test_standalone_pi5_service_matches_same_model_cadence():
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    service = (deploy / "wifiguard-pi5-onnx.service").read_text(encoding="utf-8")
    arguments = shlex.split(next(line.removeprefix("ExecStart=")
                                for line in service.splitlines()
                                if line.startswith("ExecStart=")))
    assert arguments[arguments.index("--stride-seconds") + 1] == "0.5"
    assert arguments[arguments.index("--threads") + 1] == "1"
