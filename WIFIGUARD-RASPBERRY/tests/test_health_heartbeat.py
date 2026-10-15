from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from wifiguard_edge.__main__ import EdgeApp


class _Alive:
    def __init__(self, alive: bool) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


def _app(path: Path, *, source_running: bool = True, loops_alive: bool = True) -> EdgeApp:
    app = object.__new__(EdgeApp)
    app._health_file = path
    app.source = SimpleNamespace(running=source_running)
    app.presence = _Alive(loops_alive)
    app.features = _Alive(loops_alive)
    app._telemetry_thread = _Alive(loops_alive)
    return app


def test_health_heartbeat_is_written_when_all_core_loops_are_live(tmp_path: Path) -> None:
    health_file = tmp_path / "edge.health"

    _app(health_file)._refresh_health()

    assert health_file.is_file()


def test_stale_health_heartbeat_is_removed_when_a_core_loop_stops(tmp_path: Path) -> None:
    health_file = tmp_path / "edge.health"
    health_file.touch()

    _app(health_file, loops_alive=False)._refresh_health()

    assert not health_file.exists()


def test_clear_health_removes_heartbeat(tmp_path: Path) -> None:
    health_file = tmp_path / "edge.health"
    health_file.touch()
    app = _app(health_file)

    app._clear_health()

    assert not health_file.exists()
