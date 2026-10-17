"""업로드 게이트 — 클라우드 비용을 결정하는 판정 로직.

게이트가 잘못 닫히면 낙상 구간을 잃고, 잘못 열리면 클라우드 CWT 비용이 그대로 늘어난다
(모델서버 1코어가 초당 약 2윈도우인데 기기당 4윈도우가 필요하다). 두 실패 모드를 모두 본다.
"""

from __future__ import annotations

import pytest

from wifiguard_edge.gating import SignalGate


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture
def clock():
    return FakeClock()


def gate(clock, **kw) -> SignalGate:
    return SignalGate(clock=clock, **kw)


def test_present_opens_with_reason(clock):
    d = gate(clock).decide("present")
    assert d.open and d.reason == "present"


def test_absent_without_prior_presence_is_closed(clock):
    """기동 후 계속 퇴실이면 올릴 이유가 없다."""
    d = gate(clock).decide("absent")
    assert not d.open and d.reason is None


def test_unknown_opens_by_default(clock):
    """재실 미상은 퇴실이 아니다 — 기동 직후 구간을 통째로 잃으면 안 된다."""
    d = gate(clock).decide(None)
    assert d.open and d.reason == "forced"


def test_unknown_can_be_closed_by_config(clock):
    d = gate(clock, publish_when_unknown=False).decide(None)
    assert not d.open


def test_absent_lingers_then_closes(clock):
    """★ 안전 규칙: 낙상하면 사람이 안 움직여 ABSENT 가 된다.

    곧바로 닫으면 정작 필요한 구간을 잃으므로 linger 만큼 더 올린다.
    """
    g = gate(clock, linger_after_absent_s=10.0)
    assert g.decide("present").open

    clock.advance(5.0)
    d = g.decide("absent")
    assert d.open and d.reason == "forced", "유예 구간에는 열려 있어야 한다"

    clock.advance(6.0)  # present 로부터 11초 — 유예 초과
    assert not g.decide("absent").open


def test_present_refreshes_linger(clock):
    g = gate(clock, linger_after_absent_s=10.0)
    g.decide("present")
    for _ in range(5):
        clock.advance(8.0)
        assert g.decide("present").open
    clock.advance(8.0)
    assert g.decide("absent").open, "직전이 present 였으므로 유예 안"


def test_force_open_overrides_absent(clock):
    """캘리브레이션은 재실과 무관하게 반드시 올려야 한다."""
    g = gate(clock, linger_after_absent_s=0.0)
    assert not g.decide("absent").open

    g.force_open(60.0)
    d = g.decide("absent")
    assert d.open and d.reason == "calibration"

    clock.advance(61.0)
    assert not g.decide("absent").open


def test_counters_report_both_sides(clock):
    """게이트가 닫혀 있었다는 사실도 관측 가능해야 한다 (telemetry.signals_gated)."""
    g = gate(clock, linger_after_absent_s=0.0)
    for _ in range(3):
        g.decide("present")
    for _ in range(7):
        g.decide("absent")
    opened, gated = g.counters()
    assert (opened, gated) == (3, 7)


def test_is_open_tracks_state_without_consuming(clock):
    """`is_open` 은 텔레메트리용 조회라 카운터를 건드리면 안 된다."""
    g = gate(clock, linger_after_absent_s=10.0)
    g.decide("present")
    before = g.counters()
    assert g.is_open
    assert g.counters() == before

    clock.advance(11.0)
    assert not g.is_open


def test_note_presence_updates_without_deciding(clock):
    """발행 루프가 재실을 알려 주면 게이트가 그것만으로 열려 있어야 한다."""
    g = gate(clock, linger_after_absent_s=10.0)
    g.note_presence("present")
    assert g.is_open
    assert g.counters() == (0, 0), "note_presence 는 판정이 아니다"
