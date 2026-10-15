# Pi5: PR 백엔드·DB·화면을 함께 실행하는 로컬 프로필

> **2026-10-15 최신 애플리케이션 배포본**: 최신 PC/모바일 웹 UI, 원본 FP32 모델,
> SMTP/FCM 의존성과 DB 0005가 포함된 ZIP 설치는 [RELEASE.md](RELEASE.md)를 우선 참고한다.
> 이 문서 아래에는 이전 단계의 실측/준비 기록도 남아 있다. 이번 변경은 실물에 배포하지 않았다.

2026-10-08 기준. 기존 Pi3B+ PR은 수정해서 올리지 않았다. 이 프로필은 별도로 선택할 때만 작동한다.
목표는 **센서 1대, 낙상 추론 0.5초 stride·모델 1스레드, 기존 재실 계산 0.5초 주기, 소수의 웹 접속자**다.
실물 Pi5의 모의 RAW 소프트웨어 E2E 처리시간은 아래처럼 확인했다. 실제 SPI 안정성·낙상 정확도는 아직 검증하지 않았다.

## 확정 운영 설정 (2026-10-08)

사용자 선택에 따라 Pi5 모델 주기를 **0.5초**로 고정했다. 실물 Pi5 8GB의 전체 소프트웨어
E2E 비교에서 0.5초·모델 1스레드는 P95 348.26ms / 최대 366.98ms, 기한 초과/예약 누락 0이었다.
0.25초의 1~3스레드 시험에서는 실제 250ms 초과와 누락이 있어 선택하지 않았다.
이 수치는 합성 WGSP RAW 입력의 65초 단기 시험이며 실물 SPI나 장기 보장을 뜻하지 않는다.

| 설정 | 확정값 |
|---|---|
| 모델 실행 간격 | `MODEL_STRIDE_SECONDS=0.5` (재실 확인 + 정상 공실 전환 뒤 추가 10초 유예) |
| 모델 계산 스레드 | `MODEL_THREADS=1` |
| 재실 상태 계산 | `--presence-stride-seconds 0.5` |
| 미세 움직임 PSD 계산 | `PRESENCE_WANDER_STRIDE_SECONDS=1` |
| 입력/모델 | Q 상위 30개 / 동일 ONNX FP32 |

Pi5 환경 템플릿, 통합/독립 ONNX 서비스, ONNX CLI 기본값과 전체 스택 검증기의 기본값을
같은 설정으로 맞췄다. 기존 Torch CLI·Pi3 PR은 변경하지 않았다. 다른 주기의 명시적 옵션은
비교·복구 용도로 유지한다. Pi5의 비공개 환경 파일은 키/장치/DB 값을 보존한 채 모델 주기만
추가하고 모델 스레드를 1로 맞춘다. 실제 Edge 서비스는 아직 설치·시작하지 않았으며
ESP32·장치 등록·보정·SPI/GPIO 확인 전 자동 시작하지 않는다.

3초 입력 창, 피처·가중치·임계값·A/B 수식은 그대로다. 주기 변경으로 업데이트 횟수 기반
판정의 실제 시간은 달라질 수 있으며 **0.5초 stride는 낙상부터 알림까지 0.5초라는 뜻이 아니다.**
기존 중심 관측 지연 1.5초도 유지한다. 상시 웹 자원 모니터링은 2026-10-10에 코드 연결을
완료했으며, 아래 새 준비/검증 범위를 따른다. 실제 Pi5에 새 버전을 설치한 것은 아니다.

## 2026-10-10: GUI 연결과 추가 10초 유예 — 설치 대기

이 변경은 Pi5가 없는 상태에서 구현/로컬 검증했다. 기존 0.5초 모델 stride, 재실 0.5초,
PSD 1초, Q 상위 30개, 모델 구조·FP32 가중치·피처·A/B 확정 수식은 유지한다.
학습/MLOps/Kafka/Docker를 다시 넣지 않았다. 기존 Pi3 PR에는 커밋/푸시하지 않았다.

| 연결한 기능 | 동작 |
|---|---|
| 장치 화면 | `SPI` 등록 → 선택한 장치를 Pi5 수신기에 연결. JWT/소유자·시설 권한 확인 |
| 수신 제어 | 시작/중지/새 보정. 한 개의 고정 systemd Edge 서비스만 조작, 임의 명령/경로 불가 |
| 실제 빈방 보정 | 화면에서 현재 실행의 빈방을 확인 → 원래 30초 퇴실 대기 + 30초 측정 → 계산값/진행 DB 저장 |
| 설정 적용 | DB 저장 → 제어 에이전트 동기화 → 모델 틱 경계에서 적용 → 동일 revision ACK를 화면에 표시 |
| 전체 자원 | Edge/API/UI/DB/nginx/제어/모니터 서비스의 cgroup CPU·메모리·스왑·누적 I/O, 온도·파일시스템 저장량 |
| 처리시간 | 기존 실행 결과의 전처리/피처/추론/전체 P95를 관제·장치·설정 화면에서 표시 |
| 추가 유예 | 정상 `present → absent` 판정 시점부터 최대 10초만 추론 계속. 반복 공실 보고로 연장하지 않음 |

추가 10초는 원래 `presence_timeout_s` 무활동 타임아웃과 별개다. 타임아웃이 기본 10초라면
마지막 움직임부터 종료까지 단순히 10초가 아니라 **퇴실 판정에 걸린 시간 + 추가 10초**가 된다.
처음부터 공실이거나 미상/CSI 지연/끊김/리셋/보정/설정 오류인 경우에는 유예하지 않는다.
정상 유예 중에는 A/B 판정 이력을 이어가며, 유예 만료나 오류 뒤 새 재실 세션에서 초기화한다.
미확인 상태 전환을 놓친 경우에도 이전 계산 결과를 발행하지 않는다.

화면의 `threshold`는 원시 확률(IDLE/SUSPECT) 임계값이다. A/B Markov·EMA·B k-of-n의
확정 임계값을 바꾸는 기능이 아니며, `cooldownSeconds`는 이후 생성되는 백엔드 알림 쿨다운에
적용한다(이미 진행 중인 쿨다운 시간을 소급 변경하지 않음). 실제 보정이 완료되기 전에는
런타임 설정 적용도 대기한다. 잘못된 파일/설정은 추론을 차단하며 마지막 유효 설정으로
몰래 계속하지 않는다. 일반 장치 PATCH로 SPI 보정 완료/기준값을 위조할 수 없다.

모니터는 2초 주기로 갱신한다. CPU는 전체 코어 용량 기준으로 표시하고, 메모리는 공유 RSS가
아닌 서로 겹치지 않는 cgroup `memory.current` 합계(청구된 파일 캐시 포함)다. 모델 GPU는
미사용이며 GPU 전체 사용률은 측정하지 않는다. 저장량은 상태 폴더가 있는 파일시스템 전체이며
DB 전용 크기가 아니다. 첫 샘플/필수 서비스 누락/오래된 상태는 0으로 꾸미지 않고 미측정으로
표시한다. OS·브라우저 사용량은 서비스 합계 밖이며 시스템 가용 RAM을 별도로 수집한다.

이번 검증: 로컬 Edge **177개**, 백엔드/API/계약/알림 **118개**, Raspberry 처리 **108개**,
프런트 **42개** 테스트 통과. Raspberry의 Linux 전용 SPI 테스트 2개는 Windows에서 제외했다.
백엔드는 운영 DB와 분리한 임시 PostgreSQL 16.6의 55482 포트에서 마이그레이션 0004,
SPI 등록/권한/실제 보정 결과 저장/중복 로그 방지/설정 revision을 확인했다.
프런트 타입 검사·빌드 통과, node_modules 없이 내보낸 Pi5 UI의 장치/설정 SSR와 JS asset은
HTTP 200이었다. 알림 전송은 기존 가짜/loopback 어댑터 시험이지 외부 메일/폰 도착 시험은 아니다.
systemd/polkit/GPIO·실물 SPI·새 상시 모니터의 Pi5 성능/발열·Timescale 재실 적재는 이번에
실물 확인하지 않았다. 위 예전 실물 측정값을 새 서비스 추가 후 성능 보장으로 재사용하지 않는다.

### 나중에 기존 Pi5에 넣는 순서

PC에서 준비한 `wifi-guard-pi5-controls-ready.zip`은 **기존 설치 위에 코드/UI만 반영하는
업데이트 묶음**이다. 새 OS 설치 파일이 아니다. 모델·가상환경·비공개 환경 파일·DB·센서
데이터는 포함하지 않는다. `PI5-CONTROLS-MANIFEST.json`에 파일별 SHA256과 검증 범위를
기록했다. 기존 `/opt/wifiguard`를 백업한 다음 그 경로 아래에만 압축을 풀어 사용한다.
압축 해제만으로 서비스가 재시작되거나 DB가 변경되지는 않는다.

1. 이 작업 사본의 코드와 새 빌드 UI를 `/opt/wifiguard`에 복사한다. 기존 모델, 비공개
   `/etc/wifiguard/pi5.env`, DB/기준값/이력은 지우거나 덮어쓰지 않는다. 테스트 출력 폴더는
   설치 코드가 아니다. UI 내보내기 산출물에는 비밀 키/원시 CSI가 포함되지 않는다.
2. Edge를 중지한 상태에서 `sudo bash /opt/wifiguard/WIFIGUARD-LOCAL-EDGE/deploy/pi5/prepare-controls.sh`.
   이 스크립트는 **로컬 관계형 DB에 additive 0004 적용**, 기존 unit/polkit 파일 백업과 설치,
   daemon-reload만 수행한다. 환경 파일·모델·기존 데이터·배선·펌웨어를 변경하거나 서비스를
   자동으로 시작/활성화하지 않는다. API의 기존 DB 코드도 함께 복사해야 한다.
3. 새 UI bundle을 `WIFIGUARD-FRONTEND`에 복사한 뒤 아래 서비스를 명시적으로 실행한다.

   ```bash
   sudo systemctl restart wifiguard-pi5-api.service wifiguard-pi5-ui.service
   sudo systemctl start wifiguard-pi5-control.service wifiguard-pi5-monitor.service
   ```

4. LAN 화면(`http://Pi5-IP:8080`) 로그인 → 장치 화면에서 SPI 장치 등록/선택 →
   **이 Pi5에 연결** → 배선/버퍼/권한을 확인한 뒤 **수신 시작** → 실제로 방을 비울 준비가
   되었을 때 **빈방 확인 · 측정 시작**. 재실/추론은 `done` 이후에 시작된다.
   GUI binding 파일이 `LOCAL_DEVICE_ID/LOCAL_TENANT_ID` 환경의 이전 값보다 우선한다.
5. 화면에서 보정 완료, 새 설정의 적용 ACK, 전체 자원 최신 여부, 센서 장애 즉시 차단과 정상
   퇴실 추가 10초를 확인한다. 성공을 확인한 뒤에만 API/UI/control/monitor의 부팅 자동 기동을
   선택한다. Edge 부팅 자동 기동을 선택해도 매번 새로운 빈방 확인이 필요하다.

이 기능은 내부망 HTTP 파일럿용이다. 인터넷에 그대로 포트를 열지 말고 원격 외부 접속은
TLS/방화벽/VPN을 별도로 준비해야 한다. UART AGC/펌웨어 플래시·Wi-Fi 변경 GUI는 이
SPI 제어 기능에 포함되지 않는다. AGC는 기존 SPI WGSP 펌웨어 계약에 `train` 명령이 없어
화면에도 미지원으로 표시하며, 필요하면 별도 펌웨어 제어 계약이 필요하다.

## 2026-10-08 실물 Pi5 첫 설치·모의 E2E

Pi5 Model B 8GB, 팬 없는 냉각 케이스, 32GB microSD에 Ubuntu Server 24.04.5 LTS ARM64를
기록하고 읽기 검증했다. 네이티브 Python 3.12.3, PostgreSQL 16.15/TimescaleDB 2.17.2,
Node 22.23.3을 설치했다. Docker/Kafka/MLOps 서버와 GPU 추론은 실행하지 않았다.
모델 FP32 해시는 그대로이며 Q 상위 30개, 모델 1초/재실 0.5초/PSD 1초를 사용했다.

실물 Pi5에서 합성 WGSP RAW를 **65초씩 두 번** 재생했다. 모델 1스레드이며 서비스는
Pi5의 4코어를 함께 사용할 수 있다. 백엔드·DB·UI·nginx를 같은 장비에서 함께 계측했다.

| 항목 | 1회 | 반복 |
|---|---:|---:|
| 전처리·피처·추론 합계 P95 / 최대 | 335.90 / 346.09 ms | 336.55 / 371.97 ms |
| 재실 계산 P95 / 최대 | 109.90 / 113.35 ms | 112.58 / 119.36 ms |
| 모델 완료 / 모델·재실 deadline 초과 | 62 / 0·0 | 62 / 0·0 |
| 전체 앱 평균 CPU · 한 코어=100% | 57.09% | 57.18% |
| 전체 앱 동시 PSS 피크 | 576.42 MiB | 576.46 MiB |
| 재실 DB 행 / 중복 제거된 주입 낙상 | 130 / 1 | 130 / 1 |
| 시험 중 최고 온도 | 47.40°C | 47.95°C |

두 시험의 기능·자원 gate가 모두 통과했다. CPU 57.18%는 4코어 전체 용량으로 나누면
약 14.30%이며 순간 최대나 특정 코어 부하가 아니다. PSS는 공유 페이지를 비례 배분한
전체 앱 합계다. OS/전역 파일 캐시·브라우저·시험 제어기·시험용 알림 수신기는 제외하며,
OS 관측 최소 가용 RAM은 두 시험 각각 약 7,020/7,005 MiB였다.
합성 RAW 생성 비용은 포함하지만 ESP32/SPI/GPIO 비용은 포함하지 않는다.

알림은 별도 확정 이벤트를 두 번 주입해 DB 중복 제거 및 기존 SMTP/푸시 어댑터의
loopback 도착을 확인했다. 모델이 실제 낙상을 알아맞혔거나 외부 이메일/폰에 도착했다는
뜻은 아니다. 65초×2 결과로 장시간 안정성·정확도나 항상 1초 이내 처리를 보장하지 않는다.
Ubuntu의 `vcgencmd`는 `/dev/vcio`에 접근하지 못해 쓰로틀링 레지스터는 미검증이다.
이를 `throttled=0`으로 보고하지 않았다.

Pi5의 실제 앱 역할(비슈퍼유저)로 마이그레이션 0003, Timescale INSERT/rollback을 확인했다.
새 독립 JWT/relay 키는 Pi5에만 0600으로 보관했다. LAN 화면·정적 파일·API health,
외부 `/internal/` 차단 및 DB/API loopback 바인딩도 확인했다. API·UI는 부팅 자동 기동으로
준비했지만 **실물 Edge 서비스는 아직 시작하지 않았다**. 장치 등록·보정·RP1 SPI/GPIO와
ESP32 연결 확인이 다음 단계다. 기존 Pi3/PR/운영 DB는 바꾸지 않았다.

근거: `validation/physical-pi5-firstboot-20261008.json`.
원본: `local-output/pi5-firstboot-20261008/physical-tests/{first-native,repeat-native}/summary.json`
및 `first-native-hardware.json`, `repeat-native-hardware.json`.

## 모델 stride 단축 비교 (2026-10-08)

같은 실물 Pi5에서 합성 WGSP RAW를 조건별 **65초씩 1회** 재생했다. 모델 FP32와
Q 상위 30개, 재실 0.5초/PSD 1초는 그대로이며 모델 stride만 바꿨다.
시험 전용 API·DB·UI·nginx를 함께 실행했고, 기존 pilot API/UI는 켜 둔 채 계측에서 제외했다.
아래 CPU/PSS는 전체 시험 앱의 자원이며 OS·브라우저·시험 제어기/알림 수신기는 제외한다.

| 모델 stride | 합계 P95 / 최대 | 완료 횟수 | deadline / 예약 skip | CPU · 4코어 전체 기준 | 동시 PSS 피크 |
|---|---:|---:|---:|---:|---:|
| 1초 | 335.27 / 350.29 ms | 62 | 0 / 0 | 14.46% | 601.09 MiB |
| 0.75초 | 347.80 / 382.54 ms | 83 | 0 / 0 | 16.99% | 615.65 MiB |
| 0.5초 | 348.26 / 366.98 ms | 124 | 0 / 0 | 22.55% | 623.93 MiB |

세 조건 모두 기능 gate를 통과했다. 각 조건에서 재실 DB 130행, 중복 제거된 주입 낙상
1행, loopback 이메일/푸시 각 1건을 확인했다. 모델이 실제 낙상을 검출한 시험은 아니다.
재실 deadline 초과, RAW protocol reject, swap 사용은 모두 0이며 최고 온도는
각각 52.9/56.2/58.4°C였다. 재실 처리 P95는 각각 114.79/131.49/136.14 ms다.

**0.5초는 단기 모의 입력 E2E에서 동작하는 후보**, 0.75초는 처리시간 여유가 더 큰
후보다. 이 비교 시점의 운영 기본 stride는 1초로 유지했다(현재 확정값은 위의 0.5초).
이 비교 당시 0.25초는 측정하지 않았으며,
아래의 추가 0.25초 시험에서 운영 후보로 권하지 않는 것으로 정리했다. 65초씩 한 번의 시험으로 장시간
보장을 하지 않으며 실제 SPI/GPIO·낙상 정확도·외부 알림 도착은 별도 확인 대상이다.
3초 입력 창과 A/B 수식/가중치를 바꾸지 않았지만 update 횟수 기반 확정의 실제 시간은
stride에 따라 달라진다. 계산 주기가 0.5초라는 것이 전체 알람 지연 0.5초라는 뜻은 아니다.

재현 도구 `python -m wifiguard_local_edge.validate_stack`는
`--model-stride-seconds 0.25`, `0.5`, `0.75`, `1`을 지원한다. 현재 기본값은 0.5초다.
관련 회귀 검사 38개 통과. 근거: `validation/physical-pi5-stride-comparison-20261008.json`.
원본: `local-output/pi5-firstboot-20261008/physical-tests/stride-comparison/`.

### 0.25초 최종 추가 시험: 모델 1·2·3스레드

전체 CPU가 남는다는 이유만으로 250ms 마감을 지킬 수 있는지 단정하지 않고,
같은 실물 Pi5·RAW replay·모델/피처/판정 수식·재실 0.5초·PSD 1초를 유지한 채
모델 ONNX 스레드만 1/2/3으로 바꿔 조건별 **65초씩 1회** 실행했다.
모델 FP32 해시는 시험 전후 동일했다. 이 시험 중 운영 환경과 기본 stride/스레드는 변경하지 않았다.

| 모델 스레드 | 합계 P95 / 최대 | 완료 횟수 | 250ms 초과 / 예약 skip | CPU · 4코어 전체 기준 | 동시 PSS 피크 | 최고 온도 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 334.65 / 364.14 ms | 124 | 124 / 124 | 21.83% | 668.28 MiB | 56.75°C |
| 2 | 243.62 / 268.49 ms | 241 | 5 / 7 | 39.79% | 669.26 MiB | 64.45°C |
| 3 | 233.81 / 252.77 ms | 246 | 2 / 2 | 45.12% | 668.63 MiB | 65.00°C |

**시험한 1~3 모델 스레드 조건 모두 0.25초 운영 gate 실패**다. 기준은 단지 평균 CPU가
낮거나 P95가 250ms 아래인 것이 아니라, 모델 P95 <= 200ms(20% 여유), deadline 초과/
예약 skip 0, 정상 구간 invalid/stale 0 및 재실 deadline 초과 0이다. 이번에는 실제 250ms
초과와 예약 skip도 발생했으므로 여유 기준만 엄격해서 실패한 것은 아니다.
1스레드는 계산 완료가 다음 예약 시각보다 늦어 사실상 약 0.5초 간격으로 실행됐다.
2/3스레드는 빨라졌지만 충분한 최악 시간 여유가 없었다. 4스레드는 시험하지 않았다.

재실 deadline 초과, RAW protocol reject, swap은 세 조건 모두 0이다. 재실 DB 행은
129/130/130개, 중복 제거된 주입 낙상 1개와 loopback 이메일/푸시 각 1개, WS 재실/낙상
갱신을 각각 확인했다. 릴레이 오류/overflow/미전송 영구 큐는 모두 0이다. 전체 gate 실패를
DB/알림 경로 장애로 해석하지 않으며 실제 모델 정확도·외부 알림 도착을 증명하지 않는다.

예약 skip 때 기존 A/B 이력을 리셋하는 보호 로직도 작동했다(124/7/2회).
이를 없애거나 판정 규칙을 바꿔 4Hz를 통과한 것처럼 만들지 않았다. **현재 최종 운영
후보는 누락 없이 통과했던 모델 0.5초 / 재실 0.5초 / PSD 1초, 모델 1스레드**다.
이 시험 후 사용자가 0.5초를 선택해 위 확정 설정에 반영했다. 실제 Edge 서비스는 ESP32 입력 확인 전 시작하지 않는다.

자원은 전체 시험 앱 합계이며 OS·브라우저·제어기/알림 수신기·별도 idle pilot API/UI는
제외한다. 실제 production systemd CPUQuota/MemoryMax를 적용한 시험도 아니다.
65초씩 1회로 장기 보장을 하지 않는다. Ubuntu 쓰로틀링 레지스터는 미측정이다.
시험 후 pilot API/UI/DB/nginx 정상 실행 및 테스트 DB 계정 NOLOGIN 복원을 확인했다.
관련 회귀 검사 17개 통과. 근거: `validation/physical-pi5-quarter-stride-20261008.json`.
원본: `local-output/pi5-firstboot-20261008/physical-tests/quarter-stride-final/`.

## Q 상위 30개 + 계산 주기 분리 후보 (2026-10-07)

Pi3 PR/펌웨어/운영 DB에는 배포하지 않았다. 새 Pi5 서비스 파일은 입력 정책을
`MODEL_CARRIER_SELECTION=q-top30`, PSD 주기를 `PRESENCE_WANDER_STRIDE_SECONDS=1`로 명시한다.
독립 런타임의 기본 입력은 여전히 `training-fixed`다. 기존 입력으로 복귀하려면 비공개
`pi5.env`에서 두 값을 각각 `training-fixed`, `0.5`로 설정한다.

- 전체 245개 RAW를 계속 수신한다. 121/122 제외 규칙은 유지하고, 최신 3초를 동일한
  320Hz 시간축으로 보간한 뒤 Q 상위 **정확히 30개**를 선택한다. Q는
  `max(이동분산)/max(mean(이동분산), 1e-8)`, 반경 0.4초, 창 끝에서는 유효 표본 수로
  정규화한다. 동점은 RAW 인덱스가 작은 순, 모델 입력은 Q 내림차순이다.
- Q는 각 모델 창마다 다시 계산한다. 30개 채널의 물리적 인덱스와 Q값을 상태 파일에
  기록한다. 모델 버전 로그/DB에는 `;input=q-top30-v1`을 붙여 기존 입력과 구별한다.
- 기존 500×30 리샘플, Q 임계값 추가 선택, PCA/PC 합성, S3/CWT/ACF, FP32 가중치,
  모델 임계값과 A/B 확정 수식은 변경하지 않는다. PCA 부호 고정·2-of-3도 도입하지 않는다.
  **입력 채널이 달라졌으므로 예측이 기존과 같다는 보장은 없으며 정확도 재검증이 필요하다.**
- 움직임과 재실 상태 갱신은 0.5초, 10초 창의 미세 움직임 PSD 계산은 1초다.
  사이의 틱은 최대 한 PSD 주기 동안만 결과를 유지한다. 센서 리셋·입력 지연·설정 변경·
  계산 오류 때 캐시를 무효화한다. 워밍업 후 PSD 주기는 마감 시각 기준으로 유지해
  작은 스케줄러 흔들림 때문에 계산이 1.5초 간격으로 밀리지 않게 한다.
- 재실 상태머신의 보정값·임계값·2초 debounce·10초 무활동 timeout은 그대로다.
  다만 샘플 유지/관측 주기 변경으로 상태 전환 시점은 달라질 수 있다. 같은 수식이라는
  뜻이지 모든 시점의 출력이 동일하다는 뜻은 아니다. 현재 모델은 재실 확인 및 정상 공실 전환 뒤
  추가 10초 유예 동안 0.5초 stride로 실행한다. 센서 오류에는 유예하지 않는다.
- 서로 다른 필터의 MV/PSD, 320Hz RAW Q/모델 native Q 결과를 억지로 재사용하지 않는다.
  대신 동일 필터 계수 설계를 크기 32의 캐시에 보관한다. 필터된 신호 자체는 매번 계산한다.

### 서버 경량화와 유지 범위

loopback API에서 이미 계약·장치·테넌트를 검증한 객체를 같은 인제스트에 바로 넘긴다.
이전의 `객체 → JSON 직렬화 → Pydantic 재검증`만 제거했다. 외부/Kafka 입력의 검증,
JWT/로컬 토큰·loopback 제한·DB 장치 권한 확인, DB ACK 후 FIFO 삭제, 중복 방지,
재실/낙상/이벤트 이력, WebSocket, 기존 알림 처리는 유지한다. DB fsync를 끄거나
알림/이력 저장을 생략해서 성능을 좋게 보이게 하지 않는다.

기존 API 1 worker, DB pool 3+overflow 1, 배치 시계열 적재, 텔레메트리 쓰기 제한,
빌드된 UI 제공 구조를 유지한다. 학습/MLOps/Kafka/Docker는 Pi5 운영 프로필에 없다.
테스트용 PostgreSQL 컨테이너는 개발 PC에서만 사용한다.

새 성능 비교는 `validation/profile_raw_cadence.py`의 `--model-carriers`와
`--wander-period`를 사용한다. 고정 입력/동일 주기, 고정 입력/PSD 분리,
Q 상위 30개/PSD 분리의 세 조건을 분리해 **입력 변경 효과를 동일 계산 최적화로 오인하지 않는다.**
각 결과의 체크포인트 해시, 실제 실행/skip 횟수, 선택 인덱스, PSD 계산/캐시 횟수,
단계별 지연, 프로세스 CPU와 RSS를 보관한다. PC 재생 성능은 Pi5 성능으로 환산하지 않는다.

### 이번 변경 후 비교 결과

i9-13900K E코어 4개(affinity 16–19), ONNX FP32 2스레드·BLAS 1스레드,
같은 합성 RAW 320Hz로 조건별 **60초씩 1회** 실행했다. 로딩/워밍업은 측정 밖이다.
GPU는 사용하지 않았다. **백엔드/API·DB·UI·릴레이·A/B 후처리·알림 및 실물 SPI는
이 자원 비교에 포함되지 않는다.**

| 측정 | 고정 30개 + PSD 0.5초 | 고정 30개 + PSD 1초 | Q 상위 30개 + PSD 1초 |
|---|---:|---:|---:|
| 평균 프로세스 CPU (한 코어=100%) | 28.02% | 24.16% | 25.39% |
| 50ms 표본 RSS 피크 | 333.25 MiB | 333.68 MiB | 336.23 MiB |
| 전처리 P95 (Q 선택 포함) | 7.58 ms | 6.83 ms | 36.93 ms |
| 개선 피처 생성 P95 | 36.58 ms | 41.04 ms | 33.07 ms |
| FP32 추론 P95 | 86.40 ms | 93.02 ms | 89.38 ms |
| 전처리·피처·추론 합계 P95 / 최대 | 125.11 / 128.56 ms | 126.49 / 144.40 ms | 149.33 / 155.37 ms |
| 측정 구간의 RAW 프레임 / 모델 예약·완료 | 19,200 / 60·56 | 19,200 / 60·56 | 19,200 / 60·56 |
| 1초 모델 deadline 초과 / source sequence gap | 0 / 0 | 0 / 0 | 0 / 0 |

주기 분리만으로 평균 CPU는 상대 **13.76%** 감소했다. Q 선택 비용까지 포함하면
고정 입력/PSD 0.5초 대비 **9.39%** 감소, RSS 표본 피크는 **2.98 MiB** 증가했다.
즉 Q 선택이 계산을 줄인 것이 아니라, PSD 계산 절약분 일부를 새 Q 선택에 사용한 결과다.
합계 P95는 실제 합계 시간을 계산한 값이며 단계 P95의 합이 아니다.

세 조건 모두 gate가 초기 `presence_error` 1틱·`absent` 3틱을 차단했고,
새 결과 억제·실행 오류·재실 계산 deadline 초과는 0이었다. 재실 계산은 120틱을 관측했고
PSD 계산/재사용은 각각 121/0, 61/60, 60/60회였다. 종료 시점의 스레드 정리 과정 때문에
앞의 두 조건은 재실 계산 통계에 1틱이 더 들어갔다. 단기 한 번의 시험이므로 P95/최대값이나
CPU 차이를 장비의 보장 성능으로 쓰지 않는다. RSS에는 합성 RAW fixture 12.83 MiB도 포함된다.

회귀 검사 **313개 통과, 실패·skip 0**: Edge·Raspberry·모델 패키지 203개,
백엔드 계약·인제스트·알림·API 110개. API는 개발 PC의 새 전용 PostgreSQL DB로 검증했다.
계약/토큰/loopback/테넌트 권한, DB 실패 후 재시도·ACK, 중복 낙상 저장/알림 방지,
재실·공실·미상·끊김 시 모델 중지/재개, Q 수식·순위·제외 인덱스·불량 입력 거부를 포함한다.
알림 테스트는 가짜 어댑터이며 실제 이메일/휴대폰 전송이나 새 Q 입력의 정확도 시험은 아니다.
프런트 코드와 API/WS 계약은 이번 변경에서 바꾸지 않았고, 프런트 빌드는 재실행하지 않았다.

근거 요약: `validation/qtop30-split-comparison-20261007.json`.
원본: `local-output/qtop30-split-20261007/{baseline-fixed-v2,split-fixed-v2,qtop30-split-v2}/summary.json`,
`edge-tests.xml`, `backend-tests.xml`. 초기 비정렬 PSD 스케줄 실험인
`baseline-fixed`, `split-fixed` 디렉터리는 최종 비교에서 제외했다.

## 무엇이 어디서 실행되는가

### E코어 1개 및 전체 로컬 E2E 자원 측정 (2026-10-07)

앞의 E코어 4개 표는 **Edge 프로세스만** 측정했다. 이제 대표 자원 수치는
`validate_stack`의 `whole_stack_resources`와 Pi5 `stack_monitor`의 전체 합계를 사용한다.
런타임의 기존 `resources`는 상세 진단용이며 `whole_stack_included=false`로 명시했다.
같은 시점의 사용량을 더하므로 각 구성요소의 개별 피크를 합한 값과 다르다.

두 최종 실행은 같은 합성 signed-int8 RAW, 실제 WGSP CRC/헤더 해석과 I/Q 진폭 변환,
320Hz 공유 버퍼, Q 상위 30개, 재실 0.5초/PSD 1초, 개선 피처, 동일 FP32 ONNX,
원래 A/B 후처리, 내구성 있는 로컬 릴레이, 인증/테넌트 확인, PostgreSQL/Timescale,
API/WS, 기존 빌드 UI, 기존 이메일/ntfy 어댑터까지 연결했다. 합성 RAW 패킷 생성 비용도
Edge 사용량에 포함하며 실물 ESP32/SPI/GPIO 소비를 측정한 것은 아니다.

| 조건 | 실제 Windows E코어 1개 | Linux VM 논리 CPU 1개 공유 |
|---|---:|---:|
| 제한 범위 | Edge + API + UI를 affinity 16, ONNX 1스레드 | Edge + API + UI + nginx + DB 모두 cpuset 16, ONNX 1스레드 |
| DB 위치 | 별도 Docker VM, 1 vCPU 제한; 자원은 합산 | 같은 VM CPU에서 함께 실행 |
| 추론 stride / 모델 완료 | 1초 / 62회 | 1초 / 62회 |
| 전처리·피처·추론 합계 P95 / 최대 | 293.33 / 367.46 ms | 86.08 / 93.14 ms |
| 재실 계산 P95 / 최대 | 105.87 / 128.61 ms | 36.73 / 46.77 ms |
| 전체 평균 CPU · 한 코어=100% | 33.44% (앱 32.61% + 별도 VM DB 0.83%) | 16.39% |
| 전체 동시 표본 메모리 중앙값 / 피크 | 598.48 / 623.54 MiB · 혼합 RSS/DB cgroup | 744.72 / 763.40 MiB · PSS |
| 메모리·CPU 계측 구간 / 표본 수 | 71.06초 / 246개 | 71.93초 / 340개 |
| 재실 시계열 / 중복 없이 기록된 합성 낙상 | 130개 / 1개 | 130개 / 1개 |
| 낙상 이메일 / 푸시 HTTP 로컬 수신 | 1 / 1 | 1 / 1 |
| 모델 deadline 초과 / 정상 구간 invalid·stale / CSI sequence gap | 모두 0 | 모두 0 |

Windows CPU-set 조회로 논리 CPU 16이 efficiency class 0의 독립 E코어임을 다시 확인했다.
**Windows DB는 그 물리 E코어에 묶이지 않는다.** 33.44%는 두 실행 환경의 CPU 시간을
한 코어 기준으로 합산한 값이지 한 물리 E코어에서 DB까지 돌린 실측은 아니다.
Linux cpuset 16도 호스트 E코어 16 또는 Pi5 ARM 코어를 뜻하지 않는다. 두 환경의 성능
차이를 Pi5 환산 비율로 쓰지 않는다. OS, 원격 브라우저, 계측기, 시험용 알림 수신 서버,
외부 클라우드 및 ESP32 하드웨어 소비는 전체 애플리케이션 합계 밖이며 별도로 구분한다.

Windows 메모리는 프로세스 RSS와 DB cgroup 사용량(캐시 포함)을 합한 혼합 지표다.
Linux PSS는 DB 공유 버퍼의 중복 계산을 줄인 값이다. Linux RSS 단순 합계 피크는
990.60 MiB지만 같은 공유 페이지를 여러 PostgreSQL 프로세스에 중복 계산하므로
RAM 실사용 대표값으로 쓰지 않는다. PSS와 cgroup 메모리도 캐시 포함 범위가 달라
직접 동일한 지표로 비교하면 안 된다. 메모리 상한이나 장시간 최악값을 보장하지 않는다.

센서 재생은 각 65초, 시작 입력창 확보 중 추론 4회는 gate가 차단했다. 프로세스 로딩과
모델 warmup은 전체 자원 계측에 포함했고 DB 스키마 생성/회원 가입/API·UI 시작 준비는
측정 전이다. 0.2초 간격을 목표로 하지만 Windows 실효 간격은 약 0.29초였다.
합계 P95는 합계 시간의 P95이며 단계 P95를 더한 값이 아니다. 모델의 3초 입력창,
1.5초 고정 관측 지연과 A/B 확정 규칙은 유지하므로 293ms를 실제 알람 지연으로 해석하지 않는다.

낙상 라우팅 시험은 모델 실행 중 **명시적으로 합성 확정 이벤트**를 2회 주입했다.
DB 저장은 1회, 기존 알림 어댑터가 SMTP/HTTP loopback 수신처에 각 1회 전달했다.
외부 이메일/휴대폰 실제 도착, 합성 신호의 낙상 정확도나 새 Q 입력의 정확도는 검증하지 않았다.
네이티브/LINUX 기능·자원 gate 모두 통과했고 회귀 테스트는 **325개 통과, 실패·skip 0**이다.
PC 환경의 단기 소프트웨어 검증이며 실제 Pi5 ARM 설치/성능·RP1 SPI·온도 안정성은 미검증이다.

최종 원본:

- `local-output/fullstack-one-core-69a9ff7ddef6/windows-ecore1/summary.json`
- `local-output/fullstack-one-core-69a9ff7ddef6/linux-vcpu1-v2/summary.json`
- 테스트 XML: `local-output/fullstack-one-core-20261007/{edge-tests,backend-tests}.xml`
- 보존용 핵심 수치: `validation/whole-stack-one-core-20261007.json`

초기 계측기 보완/정적 asset 경로 시험의 실패 출력은 최종 결과에서 제외했으며 원본을 덮어쓰지 않았다.
전용 시험 DB/컨테이너만 정리하며 기존 운영 DB·컨테이너·PR은 변경하지 않는다.

#### 재현 및 Pi5 준비

PC의 기존 lean `.venv-onnx`, Docker와 빌드된 UI가 준비된 상태에서 실행한다.
스크립트는 사용 가능한 실제 E코어 **1개를 자동 선택**하며 새 전용 시험 DB만 생성한다.
Linux 시험은 선택 옵션이고, 컨테이너는 PC 시험 도구이지 Pi5 배포 방식이 아니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File WIFIGUARD-LOCAL-EDGE\validation\run-one-ecore-stack.ps1 -LinuxFullStack
```

Pi5 운영은 기존 네이티브 Python/Node/PostgreSQL/systemd 프로필을 사용한다.
`MODEL_THREADS=1`을 환경 파일과 서비스에 반영했고 설치 warmup도 1스레드로 맞췄다.
이는 모델 스레드 수이며 Pi5의 SPI/재실/DB/API 전체를 한 코어에 가두는 설정은 아니다.
Pi5의 실제 4코어를 서비스가 함께 활용한다. 모델 구조·FP32 가중치·후처리 규칙은 유지한다.

실물에서 설치/등록 후 서비스가 모두 실행된 상태로 아래 **읽기 전용** 측정기를 사용한다.
실제 클러스터 이름이 다르면 `--postgres-unit`을 바꾼다. `postgresql.service`는 여러 배포판에서
oneshot 부모여서 실제 DB 소비를 담지 않는다. 이 도구는 SPI/GPIO를 열거나 DB를 변경하거나
서비스/affinity를 바꾸지 않으며, 없는 서비스와 재시작/계수 리셋은 성공이나 0 사용량으로 처리하지 않는다.

```bash
sudo /opt/wifiguard/WIFIGUARD-LOCAL-EDGE/.venv-onnx/bin/python \
  -m wifiguard_local_edge.stack_monitor \
  --postgres-unit postgresql@16-main.service --duration 65 \
  --output-dir /var/lib/wifiguard-pi5-stack
```

새 `status.json`은 매초 전체 및 Edge/API/UI/nginx/DB별 CPU·메모리·swap·DB I/O와
현재 Edge 처리 상태를 담고 `summary.json`은 평균 CPU와 **동시 합계** 메모리 분포를 담는다.
Pi5 cgroup v2의 `memory.current`는 서비스에 청구된 파일 캐시도 포함한다. OS의 남은 RAM,
전체 swap/온도는 별도 항목이다. 모델 GPU 사용은 false이며 시스템 GPU 사용량은 미측정(null)이다.
systemd MemoryMax/CPUQuota 설정값을 실측 사용량으로 표시하지 않는다.

```text
ESP32 수신기 ── Batch8 SPI ── Pi5의 공유 CSI 버퍼
                              ├─ 기존 재실 계산 수식 (Pi5 실행 주기 0.5초)
                              │       └─ 재실 확인/정상 퇴실 추가 10초 → 피처 + 기존 FP32 모델 (0.5초)
                              └─ 로컬 전송 스레드 → 기존 PR API (loopback)
                                                   ├─ PostgreSQL: 사용자/기기/낙상 이력
                                                   ├─ TimescaleDB: 재실 시계열
                                                   ├─ WebSocket → 기존 프런트 화면
                                                   └─ 기존 이메일/푸시 알림 처리
LAN 브라우저 ── nginx :8080 ── 기존 빌드 화면 + API + WebSocket
```

Kafka, MQTT 브로커, Docker, MLflow, 학습·파인튜닝 서비스는 Pi5에서 실행하지 않는다.
기존 메시지 계약, DB 스키마, API 인증·테넌트 분리, 낙상 이력·알림 처리는 재사용한다.
SQLite는 DB를 대체하지 않는다. API가 잠깐 중단되어도 추론 결과를 보관하는 작은 전송 대기열이다.

## 재실 → 낙상 추론 → DB → 알림 실행 조건

Pi5 로컬 통합 프로필(`--backend-url`)은 **재실이 확인된 동안에만** 모델용 창 복사·피처 생성·추론을 실행한다.
재실 계산과 SPI 수집은 공실이어도 계속 실행된다. 기존 Pi3 실행과 백엔드 브로커 모드,
백엔드 연결 없는 독립 모델 벤치마크의 실행 조건은 변경하지 않았다.

Pi5의 재실 실행 주기는 `--presence-stride-seconds 0.5`(기본값)로 선택한다.
비교용으로 0.25초도 지원하며 낙상 모델의 `--stride-seconds 0.5`와 별도로 설정한다.
3초/10초 신호 창, 보정값, 무활동 타임아웃, 모델과 피처 수식은 유지한다.
재실 결과의 750ms 유효기간도 유지하므로, 지연이 길면 기존처럼 추론을 차단한다.
`presence_metrics`의 `stride_seconds`, `target_hz`, `deadline_misses`는 실제 선택 주기를 따른다.
재실 전환 관측 시점은 달라질 수 있으며 실제 재실/낙상 정확도가 동일하다는 뜻은 아니다.

### 2026-10-07 주기 비교: PC RAW 소프트웨어 재생

i9-13900K E코어 4개(affinity 16–19), ONNX 2스레드·BLAS 1스레드에서
합성 WGSP RAW 320Hz를 재생하면서 재실·개선 피처·동일 FP32 모델을 함께 실행했다.
조건당 60초씩 1회, warmup/모델 로딩 제외이며 **Pi5·실물 SPI·백엔드/API·DB·UI·릴레이·AB 후처리·알림은 미포함**이다.

| 측정 | 재실 0.25초 | 재실 0.5초 |
|---|---:|---:|
| 평균 프로세스 CPU (한 코어=100%) | 39.34% | 27.16% |
| 50ms 표본 RSS 피크 | 333.53 MiB | 335.34 MiB |
| 재실 계산 횟수 | 240 | 120 |
| RAW 프레임 | 19,200 | 19,200 |
| 모델 예약/완료 | 60/56 | 60/56 |
| 전처리·피처·추론 P95 / 최대 | 130.42 / 152.00 ms | 123.79 / 138.56 ms |
| 1초 모델 deadline 초과 / source sequence gap | 0 / 0 | 0 / 0 |

평균 CPU는 상대 30.97% 감소했다. 두 조건 모두 gate의 `presence_error` 1회·`absent` 3회로
추론 4회는 수행하지 않았다. RSS에는 미리 생성한 합성 입력 12.83MiB 및 라이브러리 등이 포함되며
생산 장비 RAM 추정치가 아니다. 단일 단기 시험이며 재실/낙상 정확도·장시간 안정성을 증명하지 않는다.

별도 단계 분리 CPU 프로파일을 초당 호출 수로 정규화하면 새 비중은 raw 3.17%,
모델 윈도 전처리 0.27%, 피처 5.80%, 추론 46.89%, 재실 43.87%다.
이 비중은 실제 전체 장치 CPU나 단계별 RAM 점유율이 아니다.

재현 스크립트: `validation/profile_raw_cadence.py`.
근거: `local-output/presence-cadence-20261007/{stages,live-025,live-050}/summary.json` 및 `comparison.json`.

| 상태 | 모델 실행 | 저장/표시 |
|---|---|---|
| 최근 유효한 `present` | 0.5초 stride로 실행 | 재실 시계열, 확정 낙상 이력/이벤트 로그, 낙상 알림 분기 |
| 정상 `present → absent` 이후 10초 이내 | 0.5초 stride 추론 계속 | 공실 시계열 유지, A/B 이력 유지 |
| 초기 `absent` 또는 추가 10초 만료 | 쉬면서 재실을 계속 계산 | 공실 시계열 유지, 현재 추론값만 비움 |
| 재실 판단 불가/계산 오류 | 실행하지 않음 | 미상 표시; 공실로 꾸미지 않음 |
| CSI 입력 100ms 초과 지연/끊김, 수신기 리셋 | 실행하지 않음 | 미상/오프라인 표시, 기존 DB 이력 유지 |
| 마지막 유효 재실 계산이 750ms 초과로 오래됨 | 실행하지 않음 | 오래된 `present`로 추론을 계속하지 않음 |

재실/공실 판정은 원래 MV/Wander와 보정값, `presence_timeout_s`를 따른다.
따라서 움직임이 멈추자마자 바로 공실이 되는 것은 아니다. 기본 무활동 타임아웃은 10초다.
정확한 재실 보정과 실제 낙상 정확도는 별도 실물 검증이 필요하다. 재실 오판으로
공실/미상이 되면 이 정책에 따라 낙상 추론도 쉬므로, 전체 안전성을 보장한다고 말할 수 없다.

모델 실행 도중 오류/센서 세션이 바뀌면 결과를 새로 발행하지 않는다. 추가 유예 만료 또는
오류 뒤 재실 복귀 시 원래 A/B 이력을 초기화한다. 관측한 정상 유예/복귀는 동일 세션으로
이어가되, 틱 사이에 놓친 상태 전환은 세션 변경으로 처리한다.
모델 가중치·피처 수식·임계값은 바꾸지 않았고, 가중치를 매번 다시 로드하지도 않는다.
최초 모델 로드/워밍업은 기동할 때 한 번 수행하며, 워밍업 결과는 낙상 판정·알림으로 발행하지 않는다.
주로 공실 동안 CPU 계산을 줄이는 변경이지 모델 메모리를 제거하는 변경은 아니다.

`status.json`의 `pi5_services.inference_gate`에 현재 실행 여부·중지 이유·건너뛴 틱·억제 결과를 기록한다.
텔레메트리에는 실제 `gate_open`과 건너뛴 횟수를 보낸다. 로컬 `events.jsonl`에는 게이트 전환을 기록한다.
재실/공실 시계열은 TimescaleDB에, 확정 낙상과 이벤트 로그는 PostgreSQL에 저장한다.
매번의 음성 추론 점수까지 모두 DB에 영구 저장하는 것은 아니다.
이미 확정되어 영구 전송 대기열에 들어간 낙상 결과는 공실 전환 후에도 재전송을 계속한다.
새 세션에서도 낙상을 감지할 수 있도록 백엔드의 일시 판정 상태만 초기화하며 누적 이력·중복 방지는 보존한다.
SMTP/푸시 수신자 설정과 인터넷 연결이 있어야 실제 알림이 도착한다.

### 재실 조건 추가 후 검증

2026-10-04, 합성 CSI replay + 원래 재실 계산 + FP32 모델 + API/DB/WebSocket/빌드 화면을
PC E코어 4개 제한에서 **65.125초** 다시 실행했다. DB CPU는 제한 밖이며 실물 Pi5 시험이 아니다.

- 초기 미상/공실 4틱은 모델을 실행하지 않았고, 재실 확인 후 추론 62개를 실행했다.
- 피처·추론·enqueue P95 **138.41ms**, 최대 **148.90ms**. 1초 주기 초과/누락 0.
- 재실 계산 P95 **95.34ms**, 최대 **120.58ms**. 250ms 초과 0.
- 재실 시계열 257행, WebSocket live 251회. 별도 합성 확정 낙상 재전송은 DB에 1번만 저장.
- 오류·전송 대기열 overflow·미전송 추론 0. 모델 출력에 실제 낙상은 없었고 외부 알림도 보내지 않았다.
- 별도 결정적 테스트는 공실→재실→미상/끊김→재실에서 추론 중지/재개, 후처리 이력 초기화,
  계산 도중 상태 변경 시 발행 억제, DB 이력/중복 방지 유지, 알림 분기 호출을 검증한다.
  이 테스트의 모델/알림은 가짜 어댑터이므로 모델 정확도·이메일/휴대폰 실제 도착 검증은 아니다.

현재 재실 조건 적용 통합 결과:
`WIFIGUARD-LOCAL-EDGE/local-output/pi5-presence-gated-4ecore-20261004/summary.json`.

추가 검증 후 회귀 테스트 **317개** 통과(백엔드 108, Raspberry 109, 로컬 Edge 65, 프런트 35).
프런트 타입 검사도 통과했다. 백엔드 API 테스트는 새 전용 테스트 DB를 사용했으며,
공실 전환 후 이력 보존, 재실 복귀 후 새 낙상 저장/이벤트 로그, 재전송 시 알림 중복 방지를 확인했다.
테스트 환경의 알림 어댑터는 가짜 전송기다. 원래 운영 DB/하드웨어/PR은 변경하지 않았다.

## 이번 최적화

| 항목 | 변경 | 유지한 것 |
|---|---|---|
| 수신/버퍼 | SPI 수신기 1개, 공유 버퍼 1개. 전체 245개 CSI를 재실과 모델이 공유 | 모델에는 기존과 동일한 30개 입력 선택 |
| 재실 계산 | 보간이 필요 없는 구간의 Python 행별 복사 제거 | 반환 배열·진단 카운터의 정확 일치 회귀 테스트 |
| 모델 | 기존 최적화된 피처 생성 + ONNX Runtime CPU FP32 | 모델 구조·가중치·임계값·A/B 후처리 수식. INT8은 사용하지 않음 |
| 통신 | Kafka 대신 같은 Pi5의 인증된 HTTP로 동일 계약 전달 | 기존 캐시·DB 적재·WebSocket·알림 분기 |
| 장애 대응 | 추론은 SQLite FIFO에 먼저 저장, ACK 뒤 삭제. 재실/텔레메트리는 최신값으로 합침 | DB 장애 시 낙상 상태와 중복 방지를 되돌리고 재시도 |
| 백엔드 | API 1 worker, DB pool 3 + overflow 1 | 기존 CRUD, JWT, HOME/FACILITY 접근 통제 |
| DB | 네이티브 PostgreSQL 서버 1개 안에 기존 역할의 DB 2개 | 관계형 이력과 Timescale 시계열 분리, fsync 유지 |
| 프런트 | PC에서 빌드한 SSR/정적 파일을 Node로 제공 | 기존 화면. Vite/npm 개발 서버는 Pi5에서 불필요 |

확정한 0.5초 stride는 원본의 0.25초 stride와 관측 시점이 다르다. 수식과 가중치를 유지해도
실시간 판정 타이밍까지 동일하다고 말할 수는 없다. 모델의 고정 중심 지연 1.5초도 그대로다.
따라서 **처리시간 0.5초 이내는 낙상 발생부터 알림 도착까지 0.5초라는 뜻이 아니다.**

전송 대기열은 추론 4,096개로 제한된다. 0.5초 stride에서 약 34분 분량이다(복구용 1초는 약 68분).
가득 차면 결과를 조용히 버리지 않고 오류를 내며 상태 보고서에 기록한다.
재실/텔레메트리는 연결 장애 때 오래된 샘플을 전부 재생하지 않는 best-effort 최신 상태다.
외부 알림 전송 보장·재시도 정책은 기존 PR 구현과 동일하며, 새로 영구 알림 큐를 만들지는 않았다.

## 이전 상시 추론 프로필 측정 결과 — 재실 조건 추가 전

Windows PC의 E코어로 사용하던 논리 CPU 16~19에 **수신/replay·모델·재실·API·UI 프로세스 전체**를 제한했다.
실제 FP32 모델, 원래 재실 알고리즘, 기존 API, 빌드 UI, 실제 PostgreSQL 16.6/TimescaleDB 2.17.2를 실행했다.
입력은 합성 CSI 320Hz다. DB만 PC의 격리 테스트 컨테이너에서 실행했으며 **DB CPU는 같은 코어 제한 밖**이다.
따라서 Pi5 ISA/클럭/RP1/DB 동시 부하를 재현한 시험이 아니다.

| 측정 | E코어 4개 제한, 모델 스레드 2 | E코어 1개 제한, 모델 스레드 1 |
|---|---:|---:|
| 입력·모델 실행 | 65.109초 / 추론 63개 | 65.219초 / 추론 63개 |
| 피처·추론·결과 enqueue P95 | 134.27ms | 292.42ms |
| 위 처리 최대 | 143.04ms | 891.52ms |
| 1초 추론 주기 초과/누락 | 0 / 0 | 0 / 0 |
| 재실 계산 P95 / 최대 | 98.58 / 120.95ms | 153.41 / 740.25ms |
| 재실 250ms 초과 | 0 | 2 |
| API 조회 P95 | 32.87ms | 30.52ms |
| 화면 SSR P95 | 28.47ms | 27.91ms |
| 종합 판정 | PC 통합 후보 통과 | 재실 주기 초과로 실패 |

전체 시작·연결·검증은 약 73초다. 장시간 안정성 시험이나 시간당 자원 측정이 아니다.
SSR 첫 요청 최대 615ms가 포함된다. API/SSR 시간은 피처·추론 시간에 더해서 직렬 처리하지 않는다.
각 프로세스와 전송 스레드가 병렬로 동작한다.

4코어 시험의 추론 경로 내부 P95는 입력 준비 14.32ms, 피처 39.59ms, 모델 forward 91.10ms다.
서로 다른 창의 P95이므로 합계가 종합 P95 134.27ms와 같지는 않다.
직전 4코어 시험도 통과했으며 종합 P95 126.01ms/최대 146.15ms였다. 두 번 모두 주기 초과 0이다.

| PC에서 측정한 프로세스 | 최대 샘플 RSS | 평균 CPU, 1코어=100% 기준 |
|---|---:|---:|
| 수신/replay + 재실 + 피처 + 모델 + relay | 332.47 MiB | 42.69% |
| 기존 API | 99.30 MiB | 5.51% |
| 빌드 UI 서버 | 57.06 MiB | 0.94% |

세 프로세스의 개별 최대 RSS를 더하면 약 489 MiB다. 동시 최대나 Pi5 총 메모리 값은 아니다.
DB·OS·파일 캐시·nginx·사용자 브라우저는 이 표에서 제외했다. GPU 모델 연산은 사용하지 않는다.
검증 DB 디스크는 초기 스키마 포함 약 19.3 MiB(관계형 + 시계열)이며, 일/월간 증가량 추정에 쓰면 안 된다.

시계열 260행 적재, WebSocket live 252회, presence/fall 상태 표시를 확인했다.
별도 합성 낙상 이벤트를 두 번 전송해 DB에는 한 번만 저장되는 것도 확인했다.
실제 모델 출력은 이번 합성 입력에서 낙상 음성이었으므로 실제 낙상 정확도 검증이 아니다.
실제 이메일/휴대폰 알림은 보내지 않았다.

원본 전체 측정 JSON은 저장소 루트의 다음 위치에 있다(로컬 산출물이라 Git 제외).

- `WIFIGUARD-LOCAL-EDGE/local-output/pi5-pr-optimized-4ecore-20261004/summary.json`
- `WIFIGUARD-LOCAL-EDGE/local-output/pi5-pr-final-4ecore-20261004/summary.json` (재실 조건 추가 전)
- `WIFIGUARD-LOCAL-EDGE/local-output/pi5-pr-optimized-1ecore-20261004/summary.json`

재실 조건 추가 전 회귀 테스트는 백엔드 계약/인제스트/알림/API 104개, Raspberry 처리 109개,
로컬 Edge 42개, 프런트 35개 통과. 프런트 타입 검사·빌드도 통과했다.
UI를 프로젝트 `node_modules` 없이 별도 폴더에서 실행해 SSR와 정적 asset 4개 응답을 확인했다.
ARM64/Python 3.12용 CPU/API 의존성은 바이너리 패키지 해시 잠금으로 준비했지만, 실제 ARM 설치는 미검증이다.
DB 신규 역할/스키마 초기화와 Alembic 0003 적용, 제한된 앱 역할의 시계열 INSERT 권한도 격리 DB에서 확인했다.
송신기가 꺼지면 로컬 heartbeat만으로 online이 유지되지 않도록 하고, 오래된 재실/낙상 실시간 값은 비운다.

## 설치 순서

다음은 `/opt/wifiguard`에 이 작업 사본을 복사한 뒤의 절차다. 2026-10-08 새 Pi5에서
OS/의존성/DB/API/UI 준비까지 수행했고, 실제 장치 등록 및 SPI Edge 실행은 남아 있다.
기존 Pi3 장치/서비스를 동시에 실행하거나 원래 운영 DB를 덮어쓰지 않는다.

1. Pi5에 64비트 OS, Python 3.12, Node 22, nginx, 네이티브 PostgreSQL 16과 TimescaleDB를 준비한다.
   SPI/GPIO 권한 그룹을 가진 `wifiguard` 사용자도 필요하다.
   이 설치 스크립트는 OS/DB 설치, 배선, 플래시, 재부팅, 자동 기동을 수행하지 않는다.
   TimescaleDB의 해당 OS/ARM64 패키지 여부를 확인하고 없으면 검증할 릴리스 태그로 소스 빌드해야 한다.
   [PostgreSQL Ubuntu/ARM64 설치](https://www.postgresql.org/download/linux/ubuntu/),
   [TimescaleDB 설치](https://docs.timescale.com/self-hosted/latest/install/),
   [2.17.2 공식 소스 빌드 조건](https://github.com/timescale/timescaledb/blob/2.17.2/docs/BuildSource.md).
2. `bash WIFIGUARD-LOCAL-EDGE/deploy/pi5/install.sh`를 실행한다.
   CPU/API 패키지만 설치하고 Torch/CUDA는 설치하지 않는다. GPIO 바인딩 소스 빌드 시 개발 도구가 필요할 수 있다.
   [ONNX Runtime 공식 CPU/ARM 설치 안내](https://onnxruntime.ai/docs/get-started/with-python.html).
3. 검증된 `model-fp32.onnx`와 대응 `model-fp32.json`을 `/opt/wifiguard/models/`에 복사한다.
   모델 SHA256: `380cfaa26890c94804a663c4f1d84f1f10c84b3d7e2c28a927f7ff1afc162cba`.
4. `create-env.py`로 **새 전용** 비밀 설정을 만든다. JWT/relay/DB 키를 서로 다르게 생성하며 출력·덮어쓰기를 하지 않는다.
   설정 파일은 `/etc/wifiguard/pi5.env`, 권한 0600으로 둔다. 실제 키는 Git에 넣지 않는다.
5. 전용 DB 클러스터에서 `postgresql.conf.example`을 참고해 loopback 접속, Timescale preload 등을 설정한다.
   `bootstrap-db.py --config /etc/wifiguard/pi5.env`를 DB 관리자로 실행한다.
   기존 역할/DB가 있으면 중단하며 변경하지 않는다. 초기화 중 실패했다면 자동 삭제/재생성하지 말고 상태부터 확인한다.
6. 비밀 설정의 `DATABASE_URL` 환경으로 기존 `WIFIGUARD-BACKEND/packages/db/alembic.ini`의 `upgrade head`를 실행한다.
   Pi3/관제 DB 대신 생성된 `wifiguard_pi5`/`wifiguard_pi5_ts`를 사용한다.
7. PC의 프런트 폴더에서 `node scripts/build-pi5.mjs`로 빌드한다. `VITE_PI5_LOCAL=1`,
   same-origin API/WS, mock OFF를 명시하므로 접속 PC의 localhost를 API로 잘못 사용하지 않는다.
   `node scripts/package-pi5.mjs NEW_OUTPUT_DIRECTORY`로 추출한 파일을 Pi5의 `WIFIGUARD-FRONTEND` 위치에 복사한다.
   Pi5에서 npm 설치나 Vite 빌드는 하지 않는다.
8. API/UI systemd 예시와 nginx 설정을 실제 경로에 맞춘다. 처음에는 API·UI만 시작해서 기존 화면으로 회원/기기를 등록한다.
   새 control/monitor unit과 제한된 polkit rule을 준비해 실행하고 장치 화면에서 SPI 장치를
   Pi5에 연결한다. 기존 환경의 기기 UUID/tenant는 GUI binding이 없을 때만 fallback이다.
9. SPI Batch8/READY 배선과 `spidev.bufsiz=8192`를 확인한 뒤 Edge를 수동 시작한다.
   통합 SPI 프로필은 아래 빈방 시작 보정이 완료되기 전 재실 판정·낙상 추론을 시작하지 않는다.
   기존 설정은 `--presence-config-dir`로 전달할 수 있지만, 시작 보정의 빈방 확인을 대신하지 않는다.
   `preflight.py`는 실물 Pi5/권한/모드/DB/API/기기 등록을 읽기만 확인하며 GPIO 출력이나 펌웨어를 바꾸지 않는다.

## 시작할 때 빈방 보정 (2026-10-10 추가)

원래 레포 `wifiguard_edge.calibration.onboarding._compute_baseline_thresholds`를 그대로 호출한다.
MV에는 기존 IQR 이상치 제거 → 평균 + 2×표준편차(최솟값 0.3), 미세 움직임에는 기존
Welch PSD 대역 에너지(최솟값 0.05)를 사용한다. 임계값 상수는 기존 `CalibrationConfig`를 따른다.
재실 MV/Wander 신호 수식·상태머신, 모델 가중치/피처/AB 후처리, Q 상위 30개 정책,
모델 0.5초·재실 0.5초·PSD 1초 주기는 변경하지 않는다. 모델 학습이나 파인튜닝이 아니다.

통합 SPI CLI와 `wifiguard-pi5-edge.service`는 `--startup-calibration empty-room`가 기본이다.
**매 실행마다 빈방 확인 → 퇴실 대기 30초 → 신선한 CSI 확인 → 빈방 측정 30초 → 계산/저장 →
재실감지 시작 → 재실 및 정상 퇴실 후 추가 10초 동안 낙상 추론** 순서다.
약 60초에 CSI 대기·계산 시간이 추가된다.
1분 전체를 측정하는 것이 아니라 원래처럼 30초 대기 + 30초 측정이다.

재부팅 시 사람이 있는 방을 빈방으로 보정하지 않도록 자동으로 시작하지 않는다.
방을 비울 준비가 되면 장치 화면에서 **빈방 확인 · 측정 시작**을 누른다.
화면이 아직 설치되지 않은 진단 상황에서는 **Pi5 SSH 터미널에서** 아래 명령도 사용할 수 있다.
이후 방에서 나와 `phase=done`까지 계속 비워 둔다. 이 확인은 운영자 판단이며 CSI로 빈방을
자동 증명하는 기능은 아니다. 이전 실행의 확인 파일은 새 실행에 재사용되지 않는다.

```bash
/opt/wifiguard/WIFIGUARD-LOCAL-EDGE/.venv-onnx/bin/python -m wifiguard_local_edge.startup_calibration --confirm-empty-room
```

진행 확인은 같은 명령에서 `--confirm-empty-room`만 빼면 된다. 별도 출력 폴더로 실행했다면
두 명령에 같은 `--output-dir`를 전달한다. 실패하면 입력/빈방 상태를 해결한 뒤 Edge를 재시작하고
다시 확인해야 한다. 새 보정 GUI는 현재 실행 ID에만 확인을 보내고 실제 진행/계산값을 표시한다.

`/var/lib/wifiguard-pi5/startup-calibration.json`에 단계·오류·실행 ID,
`presence-calibration.json`에 계산한 재실 임계값/설정·입력 품질·시각을 원자적으로 저장한다.
`status.json`의 `pi5_services.startup_calibration`과 회전 로그 `events.jsonl`에도 진행을 기록한다.
원시 CSI는 저장하지 않는다. 보정 버퍼는 측정용으로 잠시 확장하고 끝나면 원래 12초 버퍼로 줄인다.
끊김, 리셋, 불완전한 측정, 20ms 초과 timestamp gap, 2% 초과 샘플 결손, 비정상 계산값,
저장 실패 시 추론 게이트는 닫힌다. 과거 보정 파일이나 기본 임계값으로 조용히 우회하지 않는다.
보정 중에는 수집/상태 보고만 진행하며 새 낙상 추론·알림은 생성하지 않는다. 기존에 확정되어
전송 대기 중인 이벤트의 재전송은 계속될 수 있다.

**UART 펌웨어 AGC 보정은 포함되지 않는다.** 원래 `run_calibration`의 `train` 명령은 UART용이고
현재 WGSP SPI 계약은 수신 전용이라 지원하지 않는다. 따라서 지원하지 않는 명령을 보내거나
AGC 성공을 표시하지 않고 `firmware_agc_performed=false`로 명시한다. 이 단계는 Pi5에서 수행하는
빈방 환경 임계값 보정이며 수신기 AGC 제어까지 필요하면 별도 펌웨어 제어 계약/검증이 필요하다.

독립 모델 벤치마크와 replay는 기본적으로 보정을 하지 않는다. replay로 이 절차 자체를 시험하려면
통합 실행에 `--startup-calibration empty-room`를 명시한다. 그 결과는 모의 입력 보정으로 표시되며
실제 빈방 보정·재실 정확도·실물 SPI 검증을 뜻하지 않는다. `--startup-calibration off`는 명시적
비교/진단용 우회이며 운영 기본값이 아니다. 기존 Pi3/PR 경로는 변경하지 않았다.

프로필의 메모리/CPU cgroup 값은 **안전 상한 예시**이며 실측 사용량·실시간 보장이 아니다.
Edge MemoryMax 1280M/CPUQuota 240%, API 512M/100%, UI 320M/50%로 분리했다.
PostgreSQL shared_buffers=128MB를 시작값으로 둔다. 실제 Pi5에서는 스왑, DB WAL/체크포인트,
동시 접속자와 ARM 모델 처리량을 함께 측정해서 조정해야 한다.

nginx는 `/internal/`를 막고 API/DB는 loopback만 연다. `--no-proxy-headers`로 헤더를 믿지 않는다.
8080 HTTP는 내부망 파일럿용이며 인터넷 노출 전 TLS/방화벽 검증이 필요하다.
로컬 학습/MLOps는 실행하지 않으며 nginx에서 `/api/v1/training`도 막는다.
원격 펌웨어 플래시·MQTT 명령·재보정 GUI 기능을 이 새 프로필에서 검증했다고 간주하지 않는다.
외부 알림은 원래 수신자/SMTP/푸시 설정과 인터넷 연결이 있어야 한다.

## 실물 Pi5에서 마지막으로 확인할 것

- SPI 실제 입력으로 0.5초 stride·모델 1스레드: 최소 30창, 처리 P95 <= 400ms, 500ms 초과/주기 누락 0.
- 선택한 재실 0.5초: P95 < 400ms, 500ms 초과 0, PSD 1초 계산/재사용, stale/오류가 상태에 반영됨.
- DB/화면을 동시에 사용하며 실제 CSI 적재·낙상 결과 상태·WebSocket 갱신 확인.
- RSS/CPU/스왑과 DB WAL/디스크, 실제 네트워크 모드 측정.
- 정확도와 외부 알림 도착시간은 별도 시험. 이번 작업은 실제 낙상 동작/장시간 온도 시험을 포함하지 않는다.

실물 Pi5가 이 기준을 통과하기 전에는 "Pi5에서도 성능 보장 완료"라고 표시하지 않는다.
