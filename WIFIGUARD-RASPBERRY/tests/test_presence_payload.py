"""`PresenceLoop._payload()` 가 `PresenceStatus` 필드명을 그대로 내보내는지 검증한다.

이 테스트가 있는 이유: 이 함수는 원래 3개를 `presence_*` 로 개명하고
`seconds_since_activity` 를 버리고 있었다. 양 끝(`PresenceStatus` dataclass 와
`presence_samples` DB 컬럼)은 이미 이름이 같았으므로, 중간의 이 리네임 하나 때문에
DB 컬럼 하나가 영원히 NULL 이 될 상황이었다. 런타임에는 아무 오류도 나지 않는 종류의
결함이라 명시적으로 단언하지 않으면 다시 생긴다.

백엔드 쪽 반대편: `WIFIGUARD-BACKEND/packages/contracts/tests/test_contract_parity.py`.
"""

from __future__ import annotations

import dataclasses

from wifiguard_edge.presence import PresenceConfig
from wifiguard_edge.presence.state_machine import PresenceState, PresenceStatus
from wifiguard_edge.presence_loop import PresenceLoop


def _loop() -> PresenceLoop:
    """스레드를 기동하지 않고 payload 만 본다. ring 은 쓰이지 않는다."""
    return PresenceLoop(ring=None, config=PresenceConfig())  # type: ignore[arg-type]


def _status(**over) -> PresenceStatus:
    base = dict(
        state=PresenceState.PRESENT,
        mv_current=3.5,
        wander_current=0.9,
        mv_threshold=2.0,
        wander_baseline=0.5,
        wander_ratio_threshold=1.8,
        wander_ratio=1.8,
        wander_confirmed=True,
        last_activity_at=1757000000.0,
        seconds_since_activity=0.25,
        just_changed=False,
    )
    return PresenceStatus(**{**base, **over})


def test_payload_keys_equal_presence_status_fields():
    """키 집합이 dataclass 필드 집합과 정확히 같아야 한다 — 한쪽만 늘어도 실패."""
    expected = {f.name for f in dataclasses.fields(PresenceStatus)}
    loop = _loop()

    assert set(loop._payload()) == expected, "미측정 상태에서도 키 집합은 같아야 한다"

    loop._last = _status()
    assert set(loop._payload()) == expected


def test_no_presence_prefix():
    """`presence_state` / `presence_mv_threshold` / `presence_just_changed` 가 되살아나지 않게."""
    loop = _loop()
    loop._last = _status()
    assert not [k for k in loop._payload() if k.startswith("presence_")]


def test_seconds_since_activity_is_not_dropped():
    """구 구현이 유일하게 통째로 버리던 필드. DB 컬럼이 존재하므로 반드시 실려야 한다."""
    loop = _loop()
    loop._last = _status(seconds_since_activity=12.5)
    assert loop._payload()["seconds_since_activity"] == 12.5


def test_values_pass_through_unchanged():
    """이름뿐 아니라 값도 변형 없이 통과해야 한다 (state 만 Enum → str)."""
    status = _status()
    loop = _loop()
    loop._last = status
    payload = loop._payload()

    assert payload["state"] == "present", "Enum 이 아니라 .value 여야 한다 (와이어는 소문자)"
    for field in dataclasses.fields(PresenceStatus):
        if field.name == "state":
            continue
        assert payload[field.name] == getattr(status, field.name), field.name


def test_unmeasured_state_is_none_not_absent():
    """한 틱도 안 돌았을 때 'absent' 로 단정하면 안 된다 — 미상과 퇴실은 다르다."""
    loop = _loop()
    payload = loop._payload()
    assert payload["state"] is None
    # 임계값은 설정에서 채워 알려 준다 (UI 가 표시할 수 있게)
    assert payload["mv_threshold"] == PresenceConfig().presence_mv_threshold
    assert payload["wander_baseline"] == PresenceConfig().wander_baseline


def test_status_includes_loop_health_plus_payload():
    """`status()` = 루프 헬스 4필드 + payload 11필드."""
    loop = _loop()
    loop._last = _status()
    status = loop.status()
    assert {"enabled", "tick_count", "skip_count", "last_error"} <= set(status)
    assert {f.name for f in dataclasses.fields(PresenceStatus)} <= set(status)
