import pytest

from wifiguard_local_edge.stack_metrics import StackSampler, simultaneous_totals
from wifiguard_local_edge.stack_monitor import aligned_delta, distinct_service_cgroups, read_cgroup


def test_missing_database_is_not_zero():
    assert simultaneous_totals({"edge": {"memory_bytes": 100}, "database": {"memory_bytes": None}}) == {
        "memory_bytes": None, "pss_bytes": None,
    }


def test_aligned_totals_not_sum_of_unrelated_peaks():
    rows = [
        {"edge": {"memory_bytes": 100, "pss_bytes": 90}, "db": {"memory_bytes": 40, "pss_bytes": 20}},
        {"edge": {"memory_bytes": 50, "pss_bytes": 40}, "db": {"memory_bytes": 80, "pss_bytes": 60}},
    ]
    assert max(simultaneous_totals(r)["memory_bytes"] for r in rows) == 140  # not 180
    assert simultaneous_totals(rows[0])["pss_bytes"] == 110


def test_unavailable_pss_does_not_become_rss():
    assert simultaneous_totals({"edge": {"memory_bytes": 100, "pss_bytes": None}}) == {
        "memory_bytes": 100, "pss_bytes": None,
    }


def test_cgroup_cpu_is_delta_not_lifetime_cpu():
    before = {"edge": {"cpu_usec": 100_000_000}, "db": {"cpu_usec": 50_000_000}}
    after = {"edge": {"cpu_usec": 100_200_000, "memory_bytes": 100, "swap_bytes": 0},
             "db": {"cpu_usec": 50_050_000, "memory_bytes": 20, "swap_bytes": 0}}
    row = aligned_delta(before, after, 1)
    assert row["total"]["cpu_percent_one_core_basis"] == 25
    assert row["total"]["memory_bytes"] == 120


def test_reset_and_missing_components_are_incomplete():
    with pytest.raises(ValueError, match="reset"):
        aligned_delta({"db": {"cpu_usec": 100}}, {"db": {"cpu_usec": 1}}, 1)
    with pytest.raises(ValueError, match="components"):
        aligned_delta({"db": {}}, {}, 1)


def test_overlapping_cgroups_rejected():
    assert distinct_service_cgroups({"edge": "/system.slice/edge", "db": "/system.slice/postgres"})
    assert not distinct_service_cgroups({"edge": "/system.slice", "db": "/system.slice/postgres"})
    assert not distinct_service_cgroups({"edge": "/same", "db": "/same"})


def test_read_cgroup_cpu_memory_and_io(tmp_path):
    directory = tmp_path / "service"
    directory.mkdir()
    (directory / "cpu.stat").write_text("usage_usec 2000\nuser_usec 1000\n")
    (directory / "memory.current").write_text("120\n")
    (directory / "memory.swap.current").write_text("10\n")
    (directory / "io.stat").write_text("8:0 rbytes=10 wbytes=20 rios=1\n8:1 rbytes=30 wbytes=40\n")
    result = read_cgroup(tmp_path, "/service")
    assert result["cpu_usec"] == 2000
    assert result["memory_bytes"] == 120
    assert result["swap_bytes"] == 10
    assert result["io_read_bytes"] == 40
    assert result["io_write_bytes"] == 60
    with pytest.raises(ValueError):
        read_cgroup(tmp_path, "/../outside")
    with pytest.raises(ValueError):
        read_cgroup(tmp_path, "/")


def test_missing_cgroup_raises_not_zero(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_cgroup(tmp_path, "/not-running")


def test_missing_database_process_marks_stack_incomplete():
    sampler = StackSampler({}, database_pid=2**29)
    row = sampler.sample()
    assert row["groups"]["database"]["memory_bytes"] is None
    assert not sampler.summary()["complete"]
    assert sampler.summary()["cpu_percent_one_core_basis"] is None


def test_timed_edge_exit_does_not_fabricate_zero_memory():
    import subprocess
    import sys
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        sampler = StackSampler({"edge": process.pid})
        sampler.sample()
        process.terminate()
        process.wait(5)
        sampler.sample()
        result = sampler.summary()
        assert result["complete"]
        assert result["sample_count"] == 1
        assert result["end_boundary_samples_omitted"] == 1
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(5)
