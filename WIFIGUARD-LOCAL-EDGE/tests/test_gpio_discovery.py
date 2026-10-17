from pathlib import PurePosixPath as Path
import sys
from types import SimpleNamespace

import pytest

from wifiguard_local_edge import runtime


def install_chips(monkeypatch, chips):
    opened = []

    class Chip:
        def __init__(self, path):
            opened.append(path)
            self.value = chips[path]
            if isinstance(self.value, Exception):
                raise self.value

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_info(self):
            return SimpleNamespace(num_lines=self.value[0])

        def get_line_info(self, offset):
            assert offset == 25
            return SimpleNamespace(name=self.value[1])

    class DevicePaths:
        def __init__(self, path):
            self.path = path

        def glob(self, pattern):
            assert self.path == "/dev" and pattern == "gpiochip*"
            return [Path(p) for p in chips]

    monkeypatch.setattr(runtime, "Path", DevicePaths)
    monkeypatch.setitem(sys.modules, "gpiod", SimpleNamespace(Chip=Chip))
    return opened


def test_auto_discovery_skips_denied_non_header_controllers(monkeypatch):
    opened = install_chips(monkeypatch, {
        "/dev/gpiochip0": PermissionError(13, "not permitted"),
        "/dev/gpiochip1": (32, "NON_HEADER25"),
        "/dev/gpiochip4": (54, "GPIO25"),
    })
    assert runtime.find_gpio_chip() == "/dev/gpiochip4"
    assert opened == ["/dev/gpiochip0", "/dev/gpiochip1", "/dev/gpiochip4"]


def test_auto_discovery_fails_if_only_denied_chips_exist(monkeypatch):
    install_chips(monkeypatch, {"/dev/gpiochip0": PermissionError(13, "denied")})
    with pytest.raises(RuntimeError, match="GPIO25 header line was not found"):
        runtime.find_gpio_chip()


def test_auto_discovery_does_not_hide_unexpected_device_errors(monkeypatch):
    install_chips(monkeypatch, {"/dev/gpiochip0": OSError(5, "IO error")})
    with pytest.raises(OSError, match="IO error"):
        runtime.find_gpio_chip()


def test_auto_discovery_skips_short_chips(monkeypatch):
    install_chips(monkeypatch, {"/dev/gpiochip0": (20, None),
                               "/dev/gpiochip4": (54, "GPIO25")})
    assert runtime.find_gpio_chip() == "/dev/gpiochip4"


def test_explicit_permission_denial_is_not_silently_skipped(monkeypatch):
    # Keep real Path for the explicitly supplied single-chip path.
    install_chips(monkeypatch, {"/dev/gpiochip0": PermissionError(13, "denied")})
    monkeypatch.setattr(runtime, "Path", Path)
    with pytest.raises(PermissionError):
        runtime.find_gpio_chip("/dev/gpiochip0")
