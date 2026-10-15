"""WIFI-GUARD 엣지 서비스 (라즈베리파이).

CSI를 SPI/UART로 받아 재실을 판정하고, 활동 구간의 피처를 추출해 클라우드로 올린다.
낙상 판정은 하지 않는다 — 피처까지만 만들어 MQTT로 발행하고, 판정은 클라우드가 한다.

명세: docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md
이식 현황: PORTING.md
"""
