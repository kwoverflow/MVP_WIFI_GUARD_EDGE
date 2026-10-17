from pathlib import Path

import pytest

from wifiguard_local_edge.validate_stack import build_edge_command, build_parser


def parse_stride(*extra):
    return build_parser().parse_args([
        "--model", "model-fp32.onnx", "--database-pid", "123",
        "--output-dir", "test-output", *extra,
    ])


def value(command, flag):
    return command[command.index(flag) + 1]


def test_default_uses_fixed_half_second_model_cadence():
    args = parse_stride()
    command = build_edge_command(args, "http://127.0.0.1:8000", Path("test-output"))
    assert value(command, "--stride-seconds") == "0.5"
    assert value(command, "--threads") == "1"
    assert value(command, "--presence-stride-seconds") == "0.5"
    assert value(command, "--presence-wander-stride-seconds") == "1"


@pytest.mark.parametrize("stride", [0.25, 0.5, 0.75, 1.0])
def test_model_stride_does_not_change_presence_or_model_contract(stride):
    args = parse_stride("--model-stride-seconds", str(stride))
    command = build_edge_command(args, "http://127.0.0.1:8000", Path("test-output"))
    assert value(command, "--stride-seconds") == str(stride)
    assert value(command, "--presence-stride-seconds") == "0.5"
    assert value(command, "--presence-wander-stride-seconds") == "1"
    assert value(command, "--model") == str(Path("model-fp32.onnx").resolve())
    assert value(command, "--model-carrier-selection") == "q-top30"
    assert value(command, "--transport") == "replay"
    assert value(command, "--replay-source") == "synthetic-wgsp"


def test_invalid_stride_is_rejected_before_database_access():
    with pytest.raises(SystemExit):
        parse_stride("--model-stride-seconds", "0.3")


@pytest.mark.parametrize("threads", [1, 2, 3])
def test_quarter_second_thread_trials_keep_presence_cadence(threads):
    args = parse_stride("--model-stride-seconds", "0.25", "--threads", str(threads))
    command = build_edge_command(args, "http://127.0.0.1:8000", Path("test-output"))
    assert value(command, "--stride-seconds") == "0.25"
    assert value(command, "--threads") == str(threads)
    assert value(command, "--presence-stride-seconds") == "0.5"
    assert value(command, "--presence-wander-stride-seconds") == "1"
