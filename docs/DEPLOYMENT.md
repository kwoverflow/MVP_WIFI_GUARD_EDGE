# Pi5 소스 배포와 운영 인수

## 현재 배포 구분 — 2026-10-17

이 문서는 개인 Edge 저장소의 `main`을 기준으로 하는 포트폴리오 소스 배포 안내입니다.
검토·머지 이력은 [PR #1](https://github.com/kwoverflow/MVP_WIFI_GUARD_EDGE/pull/1),
기능·검증 범위는 [최신 반영·검증](RELEASE-20261017.md)을 확인하세요.
`git clone https://github.com/kwoverflow/MVP_WIFI_GUARD_EDGE.git`으로 기본 브랜치를 받습니다.

기존 장비를 인계하는 조원은 별도로 전달한 2026-10-17 준비 장비용 ZIP을 사용합니다.
그 ZIP에는 빌드된 웹·승인된 모델·ARM64 휠·Android APK가 들어 있지만 공개 Git에는 없습니다.
푸시 시연용 PRIVATE ZIP의 암호화 서버 키와 해제 암호는 **이 공개 저장소에 올리지 않습니다.**
그 설치기는 기존 Ubuntu 24.04 Pi5와 플래시된 ESP32 쌍을 전제로 하며 새 SD/빈 OS 설치기가 아닙니다.
해당 새 설치기·만료 타이머·조원 휴대폰 수신은 아직 실물로 재검증하지 않았습니다.

## 공개 소스와 완성 ZIP

공개 저장소에는 검토·수정 가능한 애플리케이션 소스, 테스트, OS 서비스 예시와 ARM64 해시 잠금 의존성을 제공합니다. 실제 ONNX/PT 가중치, 휠, 빌드 산출물, `.env`, Firebase 서버 키, 기기 등록 토큰, DB와 RAW 기록은 제외합니다.

기존 `WIFI-GUARD-Pi5-20261015.zip`은 별도로 보관한 완성 배포본입니다. 이 PR에서 수정한 ACK 완료 알림 재노출 방지 UI는 기존 ZIP에 소급 반영하지 않았습니다. 최신 소스로 새 UI/ZIP을 빌드해야 이번 수정도 적용됩니다. 기존 ZIP은 덮어쓰지 않았습니다.

## PC에서 최신 UI 만들기

Node 24에서 저장소 루트를 기준으로 실행합니다. 빌드는 실제 API·동일 origin, 로컬 Pi5 제어, 모의 입력 비활성으로 고정합니다.

```sh
cd WIFIGUARD-FRONTEND
npm ci --no-audit --no-fund
npm run typecheck
npm test
node scripts/build-pi5.mjs
node scripts/package-pi5.mjs /absolute/new-output/ui
```

새 출력 경로만 허용합니다. `.output/pi5-build.json`의 `mock:false`, `pc_simulation:false`, `api_base:same-origin`을 확인합니다. 내보낸 폴더는 node_modules 없이 `node scripts/serve-pi5.mjs`로 실행할 수 있습니다. Node 22는 이전 ZIP의 타깃 기록이며, 이번 소스 빌드/HTTP 확인에 사용한 버전은 Node 24입니다.

## 모델과 의존성

`models/README.md`에 맞는 원본 FP32 모델을 별도 경로로 준비합니다. 파일명만 같은 임의 모델로 바꾸지 않습니다. Pi5 installer는 Python 3.12/Linux ARM64를 확인하고 `requirements-arm64.lock`과 `requirements-gpio.lock`의 해시를 검사합니다. wheelhouse가 없으면 인터넷에서 해당 잠금 패키지를 가져옵니다. 오프라인 설치에는 승인된 ARM64 wheelhouse를 별도로 전달해야 합니다.

`WIFIGUARD-LOCAL-EDGE/pyproject.toml`의 일반 환경은 연구·회귀·export용 Torch를 포함합니다. Pi5 운영 설치에는 반드시 `deploy/pi5/install.sh`의 lean ONNX 경로를 사용합니다. API가 호환성 때문에 작은 Kafka/MQTT Python 라이브러리를 import할 수 있지만 브로커/JVM을 운영하는 구조는 아닙니다.

## 새 설치와 업데이트

자세한 실제 명령은 `WIFIGUARD-LOCAL-EDGE/deploy/pi5/RELEASE.md`를 참고합니다. 준비 항목은 Ubuntu 24.04 ARM64, Python 3.12, Node runtime, PostgreSQL 16 + TimescaleDB, nginx, SPI/GPIO 권한입니다. 모델·빌드 UI·의존성을 포함한 완성 ZIP을 만들려면 같은 폴더의 `package-release.py --help`를 참고하고, 검증을 통과한 증거 파일을 제공해야 합니다.

신규 설치에서만 `create-env.py`로 JWT/relay/DB 키를 생성합니다. 기존 환경 파일은 덮어쓰지 않습니다. `bootstrap-db.py`도 이미 있는 DB/역할을 자동 삭제·재생성하지 않습니다. 환경은 `/etc/wifiguard/pi5.env`, 서버 키는 저장소 외부의 보호된 경로에 보관합니다.

업데이트는 DB 두 개와 모델·환경·상태 디렉터리 및 기존 서비스 설정 백업부터 시작합니다. 수신 서비스를 중지한 뒤 코드와 빌드 UI를 반영하고 installer, `prepare-controls.sh` 순으로 실행합니다. 후자는 additive migration 0005, unit/polkit 백업·설치와 daemon-reload를 수행하며 자동 시작하지 않습니다. nginx도 `nginx -t` 후 별도로 reload합니다. 실제 빈방 보정을 완료하기 전에는 추론을 시작하지 않습니다.

로컬 control agent는 고정 서비스와 경로만 다룹니다. API는 worker 1개로 기동합니다. API/relay/DB를 인터넷에 그대로 공개하지 않습니다. 외부 이력 조회는 HTTPS 또는 VPN을 별도로 준비합니다.

## 알림 인수

이메일은 백엔드 → SMTP → 수신함, Android 푸시는 백엔드 → Firebase FCM → 휴대폰입니다. 두 경로는 별개입니다. FCM 서버 키를 프런트나 APK에 넣지 않습니다. 앱/서버 Firebase 프로젝트 일치와 앱의 로그인·권한·기기 등록이 필요하며 PC DB의 휴대폰 등록은 Pi5로 자동 이전되지 않습니다.

`notification-preflight.py`는 외부 발송 없이 필수 설정과 키 형식을 검사합니다. 성공해도 실제 도착 성공은 아닙니다. 실제 발송을 켜기 전에 수신자와 서비스 계정 권한을 확인합니다. FCM 접수, OS 표시, 담당자 ACK도 별도 상태입니다.

네이티브 Pi5에서 수동 알림만 허용하는 설정도 추가했습니다. 기본값은
`MOBILE_PUSH_ENABLED=0`, `MOBILE_NATIVE_TEST_PUSH_ENABLED=0`입니다.
승인된 수동 시연에서는 자동 푸시는 0으로 유지하고 native 허용 플래그와 Unix 만료 시각
`MOBILE_NATIVE_TEST_PUSH_EXPIRES_AT`를 함께 설정합니다. 코드 상한은 12시간이고,
비공개 인계 도우미는 그보다 짧은 **8시간**을 사용합니다. 잘못된/만료된 허용은 등록·새 발송·대기 발송을 차단하며,
낙상 연계 메시지는 수동 모드에서 발송하지 않습니다. 기존 PC 예외와 분리되어 실제 센서/추론/DB 경로를 모의 모드로 바꾸지 않습니다.

서버의 만료 차단과 Google 키 폐기는 별개입니다. 공개 코드에는 로컬 JSON 키 삭제가
Google IAM 키를 폐기한다는 기능/보장이 없습니다. 비공개 인계 도우미의 종료 절차 후에도
프로젝트 소유자가 정확한 Google 키를 폐기해야 하며, 같은 키를 쓰는 다른 서버도 영향을 받습니다.

## 실물 인수 체크

- ARM64 의존성 설치·모델 로딩·워밍업, GPIO chip 탐색과 SPI mode/버퍼/배선.
- 실제 CSI 연속 수신·순번/CRC·손실, 빈방 30+30초 보정과 revision ACK.
- 재실/퇴실·미상/끊김 차단, 정상 퇴실만 추가 10초, 담당자 ACK 이후 재알림 방지.
- 같은 Pi5의 API·DB·UI·모니터를 포함한 처리시간/기한 누락/메모리/저장량/온도·쓰로틀링.
- DB 보존/재시작/네트워크 단절 복구, SMTP 실제 수신과 잠금 화면 FCM 수신.
- 실제 낙상·비낙상 데이터로 Q 상위 30개 입력 정책과 A/B 판정 정확도 검증.

이 목록은 이번 PR에서 실물 검증 완료를 선언한 목록이 아니라 이후 운영 승인 조건입니다.
