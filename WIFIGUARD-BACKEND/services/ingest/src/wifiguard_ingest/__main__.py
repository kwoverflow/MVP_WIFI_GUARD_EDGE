"""별도 프로세스로 인제스트만 돌린다 — `python -m wifiguard_ingest`.

**기본 배치는 이게 아니다.** 평소에는 `services/api` 의 lifespan 이 같은 `start_all()` 을
부르고, 그래야 REST 핸들러가 `LiveCache` 를 동기로 읽고 WS 가 인메모리로 팬아웃할 수 있다.

이 진입점은 두 경우를 위한 것이다.
- 적재 부하가 커져 API 와 분리해야 할 때 (M6). 그때 이 파일이 이미 있으므로 코드 변경이 없다.
- 적재만 확인하고 싶을 때 (API 를 띄우지 않고 Kafka→DB 경로만 검증).

**제약**: 이 프로세스에는 asyncio 루프가 없어 `LiveHub` 팬아웃이 동작하지 않는다.
쪼갠 뒤에도 WS 를 쓰려면 Redis pub/sub 이 필요하다.
"""

from __future__ import annotations

import logging
import sys

from .service import run_forever
from .settings import IngestSettings


def main(argv: list[str] | None = None) -> int:
    del argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
    )
    settings = IngestSettings.from_env()
    if not settings.any_enabled:
        print(
            "MQTT_HOST 도 KAFKA_BOOTSTRAP 도 설정되지 않았다 — 할 일이 없다.",
            file=sys.stderr,
        )
        return 2
    logging.getLogger("ingest").warning(
        "별도 프로세스 모드: WS 팬아웃이 동작하지 않는다(asyncio 루프 없음). 적재 전용."
    )
    run_forever(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
