# WIFI-GUARD Pi5 애플리케이션 배포본 — 2026-10-15

이 ZIP은 **기존 Pi5용 64비트 OS 위에 설치하는 전체 애플리케이션**입니다.
OS 이미지나 자동 펌웨어 설치기가 아닙니다. 압축 해제만으로 서비스를 시작하지 않습니다.
실물 Pi5·ESP32를 연결하거나 변경하지 않고 PC에서 빌드·검증했습니다.

## 포함된 기능과 파일

- 최신 빨간색 브랜드 PC UI 및 반응형 모바일 웹 UI. 같은 계정·API·DB를 사용합니다.
- Python API, 관계형 DB 마이그레이션 **0005까지**, Timescale 재실 적재 스키마.
- 원본 ONNX **FP32 모델과 대응 JSON**, CPU 추론, 모델 0.5초·재실 0.5초·PSD 1초.
- Pi5에서 Q 상위 30개 선택, 원래 피처·모델·A/B 후처리 유지. 입력 변경에 따른 정확도 동등성은 별도입니다.
- 매 실행 빈방 확인 → 30초 퇴실 대기 + 30초 측정 → 재실 게이트. 정상 퇴실 뒤 추가 10초 유예.
- GUI 장치 연결·수신 시작/중지·보정·설정 적용 ACK, 서비스 전체 자원 모니터링.
- 이메일 SMTP, Android FCM 알림·재시도 outbox·기기 등록·수신 보고·운영자 확인, 기존 ntfy 경로.
- ARM64/Python 3.12 해시 잠금 의존성 및 설치 휠, 설치 스크립트, systemd·nginx 예시, 무전송 알림 설정 검사기.

**제외**: 실제 환경 파일·JWT·SMTP 비밀번호·Firebase 서비스 계정 키·휴대폰 토큰,
DB 파일/덤프·원시 CSI·학습자료·가상환경·node_modules·Docker/Kafka/MLOps 서버.
알림은 설정을 마칠 때까지 기본 비활성입니다. 이는 발송 코드가 빠졌다는 뜻이 아닙니다.
Android APK는 Pi5에 설치하지 않습니다. 기존 휴대폰 앱을 별도로 사용합니다.

## 1. 설치 조건

대상은 Ubuntu Server 24.04 ARM64, Python 3.12, Node 22, PostgreSQL 16 + 네이티브 TimescaleDB입니다.
기존 Pi5에 준비한 기반을 재사용합니다. 다른 OS/아키텍처는 이 배포본의 검증 범위가 아닙니다.
Linux `wifiguard` 서비스 계정, `spi`·`gpio` 그룹, nginx·polkit이 필요합니다.
해당 플랫폼용 GPIO 포함 Python 의존성 휠을 동봉했습니다. 배포 ZIP의 설치기는 이를 사용하므로
Python 의존성을 다시 다운로드하지 않습니다. 기반 OS 패키지는 포함하지 않으며,
SMTP·FCM의 실제 전송에는 인터넷이 필요합니다. 소스 사본만으로 설치해 wheelhouse가 없으면 PyPI를 사용합니다.
코드 설치는 `--no-deps --no-build-isolation`을 사용합니다. 일반 `pip install -e .`로
대체하면 개발용 Torch 등의 무거운 의존성이 설치될 수 있습니다.

## 2. PC에서 압축 무결성 검사

신뢰하는 경로로 받은 SHA256 파일과 ZIP의 해시를 먼저 비교합니다.
ZIP을 새 폴더에 풀고 다음 명령을 실행하면 내부 파일·모델·운영 UI를 추가로 검사합니다.

```text
python WIFIGUARD-LOCAL-EDGE/deploy/pi5/verify-release.py .
```

내부 manifest 검사는 변조 방지 서명이 아닙니다. 외부 SHA256도 신뢰하는 출처에서 받아야 합니다.
`valid: true`는 파일/설정 프로필 검사 결과이며 실물 성능이나 알림 도착의 증명이 아닙니다.

## 3. 기존 Pi5 업데이트 절차 — Pi5에서만 실행

아래는 **나중에 Pi5에서 실행할 명령**입니다. 이번 준비 작업에서는 실행하지 않았습니다.
먼저 Edge/API/UI/control/monitor를 중지하고 기존 코드·모델·비공개 환경 파일·DB를 백업합니다.
백업 경로는 기존 백업과 겹치지 않는 새 경로를 선택하고, 환경 파일/DB 덤프는 0700 디렉터리에
비공개로 보관합니다. DB는 중지할 필요 없이 `pg_dump -Fc`로 일관성 있는 백업을 만듭니다.
DB 백업 대상은 `wifiguard_pi5`와 `wifiguard_pi5_ts` 두 개입니다.

```bash
sudo systemctl stop wifiguard-pi5-edge.service wifiguard-pi5-control.service wifiguard-pi5-monitor.service
sudo systemctl stop wifiguard-pi5-api.service wifiguard-pi5-ui.service
```

백업을 확인한 뒤 ZIP을 **새 임시 디렉터리**에 풉니다. 그 안에서 무결성을 검증하고,
확인한 파일만 `/opt/wifiguard`에 반영합니다. `/etc/wifiguard/pi5.env`, 기존 DB,
`/var/lib/wifiguard-pi5` 상태/이력은 지우지 않습니다. 새 빈방 보정은 다음 수신 시작 시 진행합니다.

```bash
python3 WIFIGUARD-LOCAL-EDGE/deploy/pi5/verify-release.py .
# 검증한 압축 해제 디렉터리 안에서 실행. 기존 /opt/wifiguard 백업을 먼저 확인합니다.
sudo cp -a . /opt/wifiguard/
sudo bash /opt/wifiguard/WIFIGUARD-LOCAL-EDGE/deploy/pi5/install.sh
sudo bash /opt/wifiguard/WIFIGUARD-LOCAL-EDGE/deploy/pi5/prepare-controls.sh
```

`install.sh`는 Python 의존성과 로컬 코드만 설치합니다. 펌웨어/배선/부팅 설정을 바꾸거나
서비스를 시작하지 않습니다. `prepare-controls.sh`는 설정 점검, 기존 DB의 additive
`alembic upgrade head`(0005까지), 기존 unit/polkit 백업·설치·daemon-reload를 수행합니다.
DB drop/truncate, 키 재생성, 서비스 자동 시작/enable은 하지 않습니다.
기존 0004 설명이 있는 과거 README보다 이 안내의 0005 절차가 우선합니다.

nginx 예시는 `deploy/pi5/nginx.conf`입니다. 기존 설정과 비교·백업한 뒤 반영하고
`sudo nginx -t`에 성공한 경우에만 별도로 reload합니다. 기존 사이트를 무작정 덮어쓰지 않습니다.
업데이트 완료 후에는 이 ZIP의 `verify-release.py`를 원본 임시 디렉터리에서 실행해
`/opt/wifiguard`의 파일 무결성도 다시 확인할 수 있습니다.

## 4. 새 설치일 때만 추가할 항목

기존 설정이나 DB가 없는 경우에만 `create-env.py --output /etc/wifiguard/pi5.env`로
새 JWT/relay/DB 키를 생성합니다. 기존 파일은 덮어쓰지 않으며 비밀 값은 출력하지 않습니다.
환경 파일은 root 소유 0600으로 유지합니다. 템플릿에는 실제 비밀 값이 없습니다.
`bootstrap-db.py --config ...`는 기존 DB/역할이 있으면 중단합니다. 초기화 실패 시 DB를
자동 삭제하거나 재생성하지 않습니다. Timescale 확장 설치·preload는 기반 OS 단계에서 준비합니다.
그 뒤 위의 `prepare-controls.sh`로 마이그레이션과 서비스 파일을 준비합니다.
자세한 초기 DB 절차는 같은 폴더의 README.md를 참고합니다.

## 5. 이메일 설정 — 백엔드 → SMTP → 메일함

기존 `/etc/wifiguard/pi5.env`를 비공개 편집기로 열어 아래 변수만 추가/수정합니다.
기존 JWT/relay/DB 값은 바꾸지 않습니다. Gmail 사용 예시는 다음과 같습니다.

```ini
PC_SIMULATION_MODE=0
MOBILE_PC_TEST_PUSH_ENABLED=0
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=1
SMTP_USERNAME=YOUR_GMAIL_ADDRESS
SMTP_PASSWORD=YOUR_GOOGLE_APP_PASSWORD
SMTP_FROM=YOUR_GMAIL_ADDRESS
```

Gmail 일반 로그인 비밀번호 대신 해당 계정의 앱 비밀번호를 사용합니다. SMTP 발신 계정은
백엔드가 인증해 사용할 계정입니다. 실제 수신자는 웹의 **알림 설정**에서 지정합니다.
파일을 채팅·공유 ZIP·프런트엔드에 넣지 않습니다. 설치 후 실제 메일 도착은 별도 확인합니다.

## 6. Android 푸시 — 백엔드 → Firebase FCM → 휴대폰

Android 앱의 Firebase 프로젝트와 같은 프로젝트의 서버 서비스 계정 키를 **별도로** 준비합니다.
서버 키는 APK나 웹 UI에 넣지 않습니다. Pi5에서 `/etc/wifiguard/firebase-service-account.json`에
보관하고 root:wifiguard 소유, 0640 권한으로 API 계정만 읽게 합니다. 상위 디렉터리도
API 계정이 통과할 수 있어야 합니다(예: root:wifiguard 0750).
환경 파일은 root:root 0600을 유지해도 systemd가 읽어 API에 전달합니다.

```ini
MOBILE_PUSH_ENABLED=1
FCM_PROJECT_ID=YOUR_FIREBASE_PROJECT_ID
FCM_SERVICE_ACCOUNT_FILE=/etc/wifiguard/firebase-service-account.json
MOBILE_PC_TEST_PUSH_ENABLED=0
MOBILE_PC_TEST_PUSH_EXPIRES_AT=
```

수신자마다 Android 앱에서 **Pi5 백엔드 주소로 로그인 → 알림 권한 허용 → 기기 등록**이 필요합니다.
PC DB의 휴대폰 등록은 Pi5 DB로 자동 이전하지 않습니다. 앱 프로젝트와 서버 프로젝트가 다르면
토큰을 공유할 수 없습니다. FCM 접수·휴대폰 수신 보고·OS 표시 보고·사람의 ‘확인함’은 서로 다른 상태입니다.

현재 Android debug 앱은 명시적인 내부망 IP의 HTTP를 지원합니다. 같은 LAN에서는
`http://Pi5-IP:8080`을 입력하고 `/api/v1`은 붙이지 않습니다. 운영 release 앱은 HTTPS가 필요합니다.
외부망에서 앱의 이력 조회/확인 처리를 하려면 HTTPS 또는 안전한 VPN 접속을 별도로 마련해야 합니다.
FCM 푸시 수신 자체는 휴대폰과 Pi5가 같은 공유기에 있어야 하는 구조가 아닙니다.
이 배포본은 포트포워딩·방화벽 약화·인증 우회·Android 보안정책 변경을 수행하지 않습니다.
모바일 **웹** UI를 열었다는 것만으로 Android FCM 기기 등록이나 브라우저 Web Push가 되지는 않습니다.

## 7. 알림 설정 검사 — 외부 발송 없음

```bash
sudo /opt/wifiguard/WIFIGUARD-LOCAL-EDGE/.venv-onnx/bin/python \
  /opt/wifiguard/WIFIGUARD-LOCAL-EDGE/deploy/pi5/notification-preflight.py \
  --config /etc/wifiguard/pi5.env --require-email --require-push
```

키 파싱·프로젝트 일치·권한·필수 설정을 확인하며 OAuth 갱신/SMTP 접속/FCM 발송은 하지 않습니다.
비밀번호·키·휴대폰 토큰은 출력하지 않습니다. 아직 알림을 설정하지 않았으면 `--require-*`를
빼서 비활성 경고만 확인할 수 있습니다. 설정 검사 성공이 실제 외부 도착 성공을 뜻하지는 않습니다.

## 8. 소프트웨어 서비스 시작과 실물 인계

```bash
sudo systemctl restart wifiguard-pi5-api.service wifiguard-pi5-ui.service
sudo systemctl start wifiguard-pi5-control.service wifiguard-pi5-monitor.service
```

이제 같은 LAN의 PC/휴대폰 브라우저에서 `http://Pi5-IP:8080`에 접속합니다.
새 DB이면 계정·대상자·SPI 장치를 먼저 등록하고 장치를 Pi5에 연결합니다.
실물 확인 전에는 Edge를 시작하지 않습니다. 소프트웨어가 시작됐다는 이유로 센서를
‘수신 중’이나 보정을 ‘완료’로 꾸미지 않습니다. 미연결이면 판단 불가/연결 대기가 정상입니다.
인터넷에 HTTP 8080이나 DB/API/내부 수신 포트를 그대로 공개하지 않습니다.

이번 배포본에는 다음 검증을 **완료했다고 표시하지 않습니다**:
새 ESP32 펌웨어/배선, RP1 SPI/GPIO 실제 수신, 실제 빈방/낙상 정확도, 실물 최신 서비스
동시 부하·발열, 장시간 안정성, 최신 Pi5에서 실제 이메일·휴대폰 도착.
예전 Pi5 단기 합성 RAW 성능 수치를 최신 버전의 보장으로 재사용하지 않습니다.
실물 검증과 장기 시험을 제외한 이번 소프트웨어 검증 근거는 `SOFTWARE-VALIDATION.json`에 구분합니다.
이번 PC 통합 검사는 관계형 PostgreSQL과 일반 재실 테이블로 수행했습니다. Windows에서
실행할 수 없는 네이티브 Timescale 확장·retention 정책, systemd/polkit·nginx 동시 실행,
ARM64 바이너리의 실제 로딩은 실행 검증하지 않았습니다. 운영 배포본은 이를 대체하지 않고
원래 Timescale 초기화 SQL과 ARM64 휠·Linux 서비스 파일을 포함합니다. 설치 시 기반 OS와
서비스 구동 확인이 필요하며, PC 통과를 해당 플랫폼의 실측 완료로 간주하지 않습니다.

## 9. 복구 원칙

이상 발생 시 새 Edge/API/UI/control/monitor를 중지하고 보관한 코드·모델·unit·환경 파일을
확인해 이전 버전으로 복구합니다. additive 0005 때문에 기존 데이터를 자동 삭제하거나
Alembic downgrade를 실행하지 않습니다. DB 복원은 백업 시각과 새로 쌓인 이력의 보존 여부를
먼저 판단한 뒤 별도 관리 절차로 수행합니다. 서비스 부팅 자동 시작은 정상 확인 후 별도로 선택합니다.
