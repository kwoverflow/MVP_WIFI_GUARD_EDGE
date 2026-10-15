"""wifiguard_contracts — 레포 간 스키마 SSOT (Pydantic v2).

- api.py      : REST 요청/응답 → FastAPI OpenAPI → 프론트 `src/api/generated/openapi.ts` 코드젠. **camelCase**
- mqtt.py     : 엣지 ↔ 클라우드 MQTT 메시지 (presence · signal · telemetry · cmd · ack). **snake_case**
- kafka.py    : Kafka 레코드 (csi-feature-stream · csi-telemetry · csi-inference-result)
- realtime.py : `/ws/live` 프레임 → `GET /realtime/schema` → 프론트 코드젠
- topics.py   : MQTT/Kafka 토픽 이름 조립·파싱

api.py 만 camelCase 인 이유는 각 모듈 docstring 참조. 요약하면, REST 는 프론트가 쓰던 이름을
유지해야 하고, 나머지는 `PresenceStatus`(엣지 dataclass) = `presence_samples`(DB 컬럼)
이름을 전 구간에서 한 번도 바꾸지 않기 위해서다.
"""
