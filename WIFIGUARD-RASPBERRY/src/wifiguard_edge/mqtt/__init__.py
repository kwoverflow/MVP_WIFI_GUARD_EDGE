"""클라우드 업링크 (MQTT over TLS 8883, 아웃바운드 전용).

    codec.py      엣지 객체 → wifiguard_contracts 메시지 → JSON 바이트 (유일한 변환 지점)
    publisher.py  논블로킹 큐 + paho 발행 스레드. 큐 포화 시 signal 부터 버린다
    command.py    cmd 구독 → 워커 실행 → ack (accepted/progress/done/error)

토픽 계약 SSOT 는 `wifiguard_contracts.topics` 다 — 여기서 문자열을 조립하지 않는다.

이 패키지는 `wifiguard-edge[mqtt]` extra 를 요구한다(paho-mqtt + wifiguard-contracts).
최상단에서 import 하지 않으므로, extra 없이도 `import wifiguard_edge` 는 성공한다.
"""
