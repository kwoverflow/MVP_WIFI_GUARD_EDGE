"""실시간 팬아웃 — `/ws/live` 와 스키마 노출.

계약은 `wifiguard_contracts.realtime` 에 있다. 구 `main.py:324-357` 의 평탄한 페이로드를
`{link, presence, fall}` 중첩으로 바꿨다 — 재실의 `mv_threshold` 와 낙상의 `threshold` 가
같은 평면에서 충돌해 `presence_*` 접두가 필요했던 문제를 구조로 없앤 것이다.
"""

from .router import router

__all__ = ["router"]
