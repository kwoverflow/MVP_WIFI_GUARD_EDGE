# WIFI-GUARD · Raspberry Pi 5 Local Edge

ESP32-C5의 CSI를 받아 Pi5 안에서 재실 감지, 낙상 추론, 이력 저장과 웹 관제를 수행하는 독립 배포 소스입니다. 클라우드/MLOps 저장소와 분리했으며 **Kafka·Docker·MLflow 서버 없이** 운영합니다. API·PostgreSQL/TimescaleDB·웹 UI는 Pi5에서 실행합니다.

이 저장소는 **소스 배포본**입니다. 모델 가중치, 인증 키, DB, 센서 데이터, ARM64 설치 휠과 빌드된 UI는 공개 Git에 포함하지 않습니다. 복제만으로 실물 센서가 동작하지는 않습니다. 별도로 제공된 원본 모델과 OS·비공개 설정이 필요합니다.

> **코드가 main에서 안 보인다면:** 현재 애플리케이션은 [Edge PR #1](https://github.com/kwoverflow/MVP_WIFI_GUARD_EDGE/pull/1)의 검토 브랜치에 있습니다. PR을 머지하기 전까지 `main`에는 초기 README만 보이는 것이 정상입니다. [실제 소스 트리](https://github.com/kwoverflow/MVP_WIFI_GUARD_EDGE/tree/codex/pi5-edge-release-20261015)에서 확인하거나 다음 명령으로 받으세요. 이 문서는 해당 검토 브랜치의 README입니다.

```sh
git clone --branch codex/pi5-edge-release-20261015 https://github.com/kwoverflow/MVP_WIFI_GUARD_EDGE.git
```

## 2026-10-17 최신 반영

- 장치 관리 Wi-Fi GUI: 임시 연결·저장·되돌리기, 보안 SSH 터널, wlan0 전용 제한 브로커.
- Pi5 GPIO chip 탐색, READY 대기·순번 손실 진단, 입력 버퍼의 반복 복사/보간 비용 개선.
- 결과를 디스크에 확정한 뒤 반환하는 저장 보장은 유지하고 ACK 정리·체크포인트·상태 파일 기록을 분리. 입력 누락/500ms 초과 원인을 단계별로 표시.
- 실제 센서 모드를 바꾸지 않는 **만료형 수동 FCM 시연**. 자동 낙상 푸시와 별도이며 기본 OFF.
- 최신 PC·모바일 웹 소스, API/OpenAPI 계약, 회귀 검사와 [최신 검증·배포 차이](docs/RELEASE-20261017.md).

개인 인계용 완성 ZIP과 공개 소스는 다릅니다. **Firebase 암호화 키 봉투·해제 암호도 공개 Git/PR에는 넣지 않습니다.** 기존 Pi5 조원 인계는 비공개로 전달한 ZIP을 사용하고, 개발자는 이 브랜치에서 소스를 검토/빌드합니다.

## 포함한 기능

- WGSP Batch8 수신·CRC/순번/타임스탬프 검사, RAW 전처리, Q-value 상위 30개 선택, PCA·S3/CWT·ACF, ONNX FP32 추론, 기존 A/B 후처리.
- 재실 판정 뒤에만 모델 실행. 정상 퇴실 판정 뒤 추가 10초 유예. 데이터 끊김·미상·리셋·보정 오류에는 유예하지 않고 추론 차단.
- 시작할 때 사용자의 빈방 확인 후 퇴실 대기 30초 + 측정 30초. 완료된 측정값과 설정 revision만 반영.
- 로그인·역할/시설 권한, 장치·재실자·낙상 이력·담당자 확인, PC/모바일 웹 UI, SMTP 이메일과 Android FCM 연동.
- Edge·API·UI·DB·nginx·제어·모니터 서비스의 cgroup 자원 합산, 시스템 가용 RAM·온도·파일시스템 용량, 단계별 처리시간.
- systemd 서비스와 제한된 로컬 제어. 화면의 시작/중지/보정은 임의 셸 명령 실행 기능이 아닙니다.

| 항목 | 기본값 |
|---|---|
| 입력 계약 | WGSP 576바이트 × 8프레임, 소스 320Hz |
| SPI | mode 0, 8bits, 6MHz, spidev.bufsiz=8192 |
| 모델 | 3초 창 → 500샘플, 동일 구조·원본 FP32 ONNX |
| 모델 실행 | 0.5초 stride, CPU 1스레드 |
| 재실 계산 / 미세 움직임 PSD | 0.5초 / 1초 |
| 백엔드 | worker 1개, PostgreSQL 16 + TimescaleDB, DB migration 0005 |

0.5초 stride는 **알림 도착 지연 0.5초 보장**이 아닙니다. 모델의 중심 관측 지연 1.5초, 후처리·전송·알림 지연은 별도로 존재합니다. Pi5 내장 GPU 가속을 전제로 하지 않습니다.

## 소스 구조

```text
ESP32-C5 → SPI/GPIO → LOCAL-EDGE
                       ├─ 재실 / 보정 / 모델 / 후처리
                       └─ 인증된 로컬 HTTP relay → API → PostgreSQL / TimescaleDB
                                                   ├─ WebSocket → PC·모바일 웹
                                                   ├─ SMTP → 이메일
                                                   └─ FCM → 등록된 Android 앱
```

`WIFIGUARD-RASPBERRY`는 재사용하는 수신·신호 처리 라이브러리입니다. 호환용 MQTT 코드는 남아 있지만 Pi5 로컬 프로필에서는 MQTT/Kafka 연결 설정을 비워 두고 브로커를 기동하지 않습니다. Android 앱 소스/APK는 이 배포 범위에 포함하지 않으며, 기존 앱은 같은 API와 Firebase 프로젝트에 등록해야 합니다. 모바일 웹을 여는 것만으로 FCM 푸시가 등록되지는 않습니다.

## 준비와 설치

1. [배포 안내](docs/DEPLOYMENT.md)에서 소스 빌드와 새 설치/업데이트의 차이를 확인합니다.
2. 보호된 경로로 모델을 전달받고 [모델 인수 기준](models/README.md)의 해시를 확인합니다.
3. PC의 Node 24에서 UI를 빌드·내보냅니다. Pi5에서 Vite 개발 서버나 npm build를 상시 실행하지 않습니다.
4. Ubuntu 24.04 ARM64 / Python 3.12와 DB·nginx·SPI/GPIO를 준비한 후 native installer를 사용합니다. **Pi5에서 일반 `uv sync`로 연구용 Torch 환경을 설치하지 않습니다.**
5. 비공개 환경, DB, 장치 등록과 실제 빈방 보정을 확인한 뒤 서비스를 명시적으로 시작합니다. 설치 스크립트는 펌웨어를 쓰거나 서비스를 자동 시작하지 않습니다.

자세한 절차는 [Pi5 배포 프로필](WIFIGUARD-LOCAL-EDGE/deploy/pi5/RELEASE.md)에 있습니다. 이 파일은 모델·휠·UI가 포함된 **별도 완성 ZIP**의 설치 방법이며, 이 공개 소스 체크아웃 자체에 ZIP manifest가 있다는 뜻이 아닙니다.

## 검증과 한계

[최신 검증 요약](docs/RELEASE-20261017.md)과 [초기 검증 보고서](docs/VALIDATION.md)에 소프트웨어 회귀 검사와 이전 배포본의 증거를 구분했습니다. 합성 RAW 재생과 낙상 결과 fixture는 **실제 사람의 낙상 정확도를 증명하지 않습니다.** Q 상위 30개 입력 정책은 기존 고정 30개 학습 입력과 별도이며 정확도 동등성이 입증되지 않았습니다. 모델 수치 회귀 통과를 이 입력 정책의 임상/현장 정확도 보장으로 해석하지 않습니다.

이번 PR에서는 실물 Pi5/ESP32 배포, 새 서비스 전체의 ARM64 장기 부하·온도·쓰로틀링, 실제 낙상·비낙상 정확도, 운영 수신자 메일·휴대폰 전달을 검증하지 않았습니다. 과거 단기 실물 측정은 당시 구성에만 해당합니다. 현재 공개 소스를 그대로 검증한 실물 성능으로 재사용하지 않습니다.

운영에서 모델/DB/비공개 환경/기준값을 백업하고, `down --volumes`, DB 초기화나 키 재생성을 업데이트 절차로 사용하지 않습니다. 개인 정보·키·실데이터는 이슈/PR에 첨부하지 않습니다.
