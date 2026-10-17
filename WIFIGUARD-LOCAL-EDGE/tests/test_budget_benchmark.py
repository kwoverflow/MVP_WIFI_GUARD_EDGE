import json
from pathlib import Path

import pytest

from wifiguard_local_edge import budget_benchmark


def test_one_core_threads_and_selected_variants_reach_fresh_workers(tmp_path, monkeypatch):
    commands = []

    def fake_run(command, *, check):
        assert check is True
        commands.append(command)
        output = Path(command[command.index("--output") + 1])
        variant = command[command.index("--variant") + 1]
        output.write_text(json.dumps({"variant": variant}), encoding="utf-8")

    monkeypatch.setattr(budget_benchmark.subprocess, "run", fake_run)
    output_dir = tmp_path / "results"
    lean_python = tmp_path / "lean-python"
    budget_benchmark.main([
        "--checkpoint", str(tmp_path / "original.pt"),
        "--model-dir", str(tmp_path / "models"),
        "--output-dir", str(output_dir), "--onnx-python", str(lean_python),
        "--cpu-affinity", "16", "--threads", "1", "--windows", "30",
        "--variants", "torch", "fp32", "fp32",
    ])
    assert len(commands) == 2
    assert commands[1][0] == str(lean_python.resolve())
    for command in commands:
        assert command[command.index("--threads") + 1] == "1"
        assert command[command.index("--cpu-affinity") + 1] == "16"
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["results"]) == {"torch", "fp32"}
    assert "1 inference threads" in summary["scope"]
    assert "30 seconds/windows" in summary["measurement"]


def test_auto_core_selection_uses_requested_threads(tmp_path, monkeypatch):
    requested = []

    def fake_select(count):
        requested.append(count)
        return [16], [{"logical": 16, "efficiency_class": 0}]

    def fake_run(command, *, check):
        output = Path(command[command.index("--output") + 1])
        output.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(budget_benchmark, "efficiency_cpu_ids", fake_select)
    monkeypatch.setattr(budget_benchmark.subprocess, "run", fake_run)
    budget_benchmark.main([
        "--checkpoint", str(tmp_path / "original.pt"),
        "--model-dir", str(tmp_path / "models"),
        "--output-dir", str(tmp_path / "results"),
        "--threads", "1", "--variants", "fp32",
    ])
    assert requested == [1]


def test_nonpositive_thread_count_is_rejected(tmp_path):
    with pytest.raises(SystemExit) as error:
        budget_benchmark.main([
            "--checkpoint", str(tmp_path / "original.pt"),
            "--model-dir", str(tmp_path / "models"),
            "--output-dir", str(tmp_path / "results"), "--threads", "0",
        ])
    assert error.value.code == 2
