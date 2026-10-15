# Windows PC에서 Pi5 로컬 통합 UI 시험

이 PC에서 Pi5용 변경 코드를 실행하는 별도 시험 프로필이다. 기존 Pi3 PR, 운영 DB,
SMTP 자격 증명, Pi5 장치에는 배포하지 않는다. 화면 숫자만 채우는 프런트 목업이 아니다.
현재 모노레포 UI의 레이아웃/장치/대상/이력/알림/설정을 그대로 사용한다.

## 지금 실행하기

저장소 루트에서 PowerShell로 실행한다. 경로에 공백이 있으므로 따옴표를 유지한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "WIFIGUARD-LOCAL-EDGE\deploy\pc\start-demo.ps1"
```

브라우저에서 **http://127.0.0.1:8095** 를 연다. 로그인 화면의 **PC 시험 계정 자동 입력**을
누르고 로그인한다. 이 프로필 전용 계정이며 운영 계정으로 사용하지 않는다.

1. 관제의 **모의 수신 시작**을 누른다.
2. **빈방 보정 시작 · 약 60초** → 확인 창의 **시험 실행**을 누른다.
   원래 코드로 30초 대기 + 30초 측정한다. 진행 중 추론을 차단하고 매 실행 새 run ID를 사용한다.
3. 완료 후 **움직임**을 누른다. RAW 변화로 재실 판단이 바뀌며 실제 피처/모델 계산이 시작된다.
4. **빈방**은 원래 무활동 타임아웃 뒤 퇴실로 바뀐다. 그 뒤에만 추가 10초 추론 유예가 적용된다.
5. **수신 끊김**은 RAW 입력을 중단한다. 센서 오류에는 10초 유예하지 않고 새 추론을 차단한다.
6. **알림 경로 시험 · 이벤트 주입**은 실제 낙상을 검출하는 시험이 아니다.
   표시된 시험 이벤트를 실제 DB/관제 알람/백엔드 알림 어댑터로 전달한다.
   알람의 확인·오탐지 처리는 실제 DB 이벤트 UUID로 저장한다. 외부 메일/휴대폰에는 보내지 않는다.
7. 알고리즘 설정에서 저장 후 **실행 코드 적용 완료**를 확인한다. 단순 DB 저장과 런타임 적용을 구분한다.

관제·장치·설정 화면의 자원 카드에서 전체 서비스 CPU/메모리, 처리 단계별 P95와 누적 I/O를 본다.
시험을 마친 뒤 Edge만 중지하려면 화면의 **수신·추론 중지**를 누른다. UI와 이력 조회는 계속 동작한다.
전체 종료는 다음 명령이다. 시험 DB와 이력은 보존되며 다음 실행 때 재사용한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "WIFIGUARD-LOCAL-EDGE\deploy\pc\start-demo.ps1" -Stop
```

코드를 수정했다면 전체 종료 후 `-BuildUI`로 다시 시작한다. 실행 중 빌드 파일을 교체하지 않는다.

## 실제로 사용하는 부분 / PC 어댑터

| 부분 | PC 실행 방식 |
|---|---|
| ESP32 수신 | 합성 I/Q → 원래 576-byte WGSP 인코딩·CRC·해석 → amplitude 버퍼. 입력만 모의 |
| 재실/보정 | 원래 이동분산·Welch PSD·상태기계, 30+30초 보정, 재실 0.5초·PSD 1초 |
| 낙상 | Q 상위 30개 → 전처리/PCA/CWT/ACF → 동일 ONNX FP32 → 기존 A/B 확정 규칙 |
| 실행 | 모델 0.5초 stride·계산 1스레드. PC 코어를 Pi5라고 가정하거나 속도를 환산하지 않음 |
| 적재 | 실제 FastAPI·JWT·스코프·계약·적재 sink·실시간 WebSocket |
| DB | 분리된 PostgreSQL 16 클러스터; 일반 재실 테이블 + 기존 관계형 DB 마이그레이션 0004 |
| 화면 | production build, `VITE_USE_MOCK=0`, same-origin. 모노레포 UI + PC 시험 패널 |
| 제어 | 고정 Edge 프로세스 시작/종료 어댑터. 운영 Pi5는 별도의 고정 systemd 서비스 |
| 알림 | 기존 백엔드 이메일/ntfy HTTP 어댑터 → PC의 loopback 수신기. 외부 발송 금지 |
| 자원 | Edge/API/UI/PostgreSQL/gateway/제어·모니터·수신기의 소유 PID 집계 |

PC 메모리는 **중복 PID를 제외한 RSS 합**이며 공유 페이지가 중복 계산될 수 있다.
CPU는 전체 PC 코어 용량 기준으로 표시한다. 브라우저와 OS 전체는 이 합계 밖이다.
Windows에서 측정하지 못한 스왑/온도/GPU 전체 사용률은 0이 아니라 미측정이다.
모델은 CPU로 실행한다. 파일시스템 사용량은 DB만의 크기가 아니다.

**미검증:** 새 변경의 실제 Pi5 성능/발열/systemd/polkit, SPI/GPIO/무선 수신과 펌웨어 AGC,
Timescale hypertable·압축·보존 정책, 장시간 안정성, 실제 낙상 정확도, 외부 메일/폰 도착.
Q 상위 30개 입력은 원래 체크포인트 입력과 수치적으로 동일하다는 주장이 아니다.
0.5초 실행 주기는 알람 지연 0.5초를 의미하지 않으며 기존 관측 지연/확정 로직은 유지한다.
학습·MLOps·Kafka·Docker는 로컬 프로필에서 제외한다. `/train`을 동작하는 것처럼 표시하지 않는다.

## 의존성이 없는 PC에 준비할 때

현재 PC의 기존 가상환경·모델·Node를 사용한다. 새 PC에는 다음이 필요하다.

- Python 3.12, Node, `uv`, 네이티브 PostgreSQL 16의 `bin` 디렉터리.
- `WIFIGUARD-BACKEND`에서 `uv sync --group dev` (실행에는 dev 테스트 도구가 필수는 아님).
- `WIFIGUARD-LOCAL-EDGE`에서 `uv sync --extra pc --extra onnx`.
- `WIFIGUARD-FRONTEND`에서 `npm ci`, 이후 `node scripts/build-pi5.mjs --pc`.
- 기존 `local-output/pi5-model-20261001/model-fp32.onnx` 및 대응 메타데이터.
  이 파일이 없으면 기존 모델 내보내기를 사용하며 임의 가중치로 대체하지 않는다.

PostgreSQL 기본 경로는 `%TEMP%\wifiguard-pg16-test-tools\pgsql\bin`이다.
다른 위치라면 시작 스크립트에 `-PostgresBin "C:\...\pgsql\bin"`을 준다.
기존 PostgreSQL/개발 Docker DB에 연결하는 것이 아니라 새 시험 클러스터를 만든다.
필요 포트는 loopback의 `8095/18095/14195/55483`; 충돌하면 실행을 중단한다.

시험 상태/DB/비공개 DB 비밀번호/로그는 `local-output/pc-pi5-demo`에만 저장한다.
해당 폴더를 PR·ZIP·외부 공유에 포함하지 않는다. 시작마다 JWT/내부 relay 키를 새로 생성하며
시험 계정 외에는 운영 비밀키를 읽지 않는다. gateway는 `/internal`을 차단한다.

## 모노레포/Pi5 반영 준비

코드 변경과 PC build를 분리했다. PC build에는 전용 로그인·RAW 시나리오 UI가 들어간다.
운영 Pi5는 `node scripts/build-pi5.mjs`로 별도 build한다. PC build를 Pi5에 설치하지 않는다.
backend의 PC 시험 endpoint는 기본 비활성이며 두 프로필을 섞은 ZIP은 패키징 단계에서 거부한다.

`package-controls.py --profile pc` ZIP은 **기존 체크아웃용 코드/UI overlay**다.
새 OS/독립 전체 설치 파일은 아니다. 모델·가상환경·DB·센서 데이터·비밀키는 제외한다.
원본 프런트 소스는 이 모노레포 변경과 PC ZIP에 있고 ZIP에는 실행용 build도 포함된다.
Pi5에 설치할 때는 `deploy/pi5/README.md`의 별도 설치·현장 검증 순서를 따른다.
이번 작업은 커밋/푸시/PR 생성이나 기존 Pi3 PR 갱신을 하지 않았다.

## 이번 PC 검증 결과 (2026-10-10)

자동 테스트 463개 통과: Local Edge 180, backend/ingest/notification/contracts 124,
Raspberry 처리 코드 110, frontend 49. 프런트 타입 검사와 PC/Pi5 production build도 통과했다.
GPIO를 모의 처리한 테스트가 포함되어 있으므로 실물 하드웨어 검증 수치로 사용하지 않는다.

브라우저에서 수신 시작, 60초 보정, RAW 움직임에 따른 재실 및 실제 ONNX 추론,
설정 저장과 실행 코드 적용, 시험 알람의 DB 적재/실시간 표시/오탐지 응답 저장을 확인했다.
백엔드 이메일·푸시 어댑터는 PC 내부 수신기에 각각 전달했다. 실제 낙상 모델 판정에 의한
외부 알림 도착을 검증했다는 뜻은 아니다. 전체 종료 후 DB와 응답 이력이 남는 것도 확인했다.

실행 중 읽기 전용 점검은 다음 명령으로 JSON 근거를 남긴다. 화면에서 보정을 완료하고
**움직임**을 선택해 실제 추론이 시작된 뒤 실행한다.

```powershell
cd WIFIGUARD-LOCAL-EDGE
.venv\Scripts\python.exe deploy\pc\check-demo.py --observe-seconds 5
```

이 스크립트는 새 시험 이벤트나 외부 알림을 발생시키지 않는다. 45초까지 관찰할 수 있으며
실행 상태, 모델 계산, DB 적재, 설정 적용, 자원 집계와 추론 게이트 변화를 확인한다.
스크립트가 통과해도 위의 실물 Pi5/펌웨어/정확도 미검증 항목은 그대로 남는다.
