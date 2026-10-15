"""인제스트 — 엣지에서 올라온 것을 저장·캐시하고 WS 로 흘려보낸다.

    MQTT ──mqtt_bridge──► Kafka ──consumers──┬─► presence_sink   → presence_samples (TimescaleDB)
                                             ├─► telemetry_sink  → Device.online / last_seen_at
                                             └─► LiveCache ─► LiveHub ─► /ws/live

**이 패키지는 라이브러리다.** 실행 주체는 `services/api` 의 lifespan 이며, 프로세스를 늘리지
않는다. 이유는 세 가지다.

1. `ResidentOut` 의 런타임 7필드를 채우려면 REST 핸들러가 최신값 캐시를 **동기로 읽어야** 한다.
   별도 프로세스면 인메모리 dict 를 못 읽으니 Redis 나 내부 HTTP 가 필요해진다.
2. WS 팬아웃도 컨슈머와 같은 주소 공간이어야 인메모리 브로드캐스트가 성립한다.
3. 현재 EC2 2대 토폴로지에서 프로세스를 늘리면 메모리가 빠듯하다.

대가: **uvicorn 워커를 1개로 고정해야 한다.** 워커가 여럿이면 각자 별도 컨슈머 그룹 멤버가
되어 메시지가 분산되고 캐시가 쪼개진다. `deploy/aws/wifiguard-api.service` 에 그 이유를
주석으로 박아 두었다.

분리 경로는 미리 확보해 두었다 — `__main__.py` 가 lifespan 과 **같은 함수**(`start_all`)를
부르므로, 부하가 확인되면 코드 변경 없이 `python -m wifiguard_ingest` 로 쪼갤 수 있다.
"""

from .service import IngestService, start_all

__all__ = ["IngestService", "start_all"]
