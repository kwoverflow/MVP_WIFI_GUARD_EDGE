# WIFI-GUARD Pi5 로컬 백엔드

이 저장소에서는 이 폴더를 **Pi5 로컬 API·DB·알림 라이브러리**로 사용합니다.
설치 기준은 [저장소 안내](../README.md)와 [소스 배포 절차](../docs/DEPLOYMENT.md)입니다.
Pi5 로컬 프로필은 HTTP relay로 수신하며 Kafka·Docker·MLOps 서버를 기동하지 않습니다.

## 원래 클라우드 구조의 참고 기록

아래 문서는 재사용 코드의 원래 개발 이력입니다. 이 공개 Edge 저장소에는 해당
클라우드 Compose/AWS 전체 배포·과거 docs 폴더나 MLOps 서비스가 포함되지 않습니다.
아래 클라우드 명령은 Pi5 설치 절차가 아닙니다. 자동 drift 재학습·배포도 이 Edge의
기능이 아닙니다. native ONNX installer와 루트의 최신 배포 안내를 사용하세요.

```
                              [ AWS IoT Core ]
                                     │ IoT Rule Engine Direct Routing
                                     ▼
                              [ Apache Kafka ]  csi-feature-stream
                                     │
      ┌──────────────────────────────┼──────────────────────────────┐
      ▼                              ▼                              ▼
 Consumer 1: Ingest           Consumer 2: Inference         Consumer 3: Drift
 (aiokafka Worker)            (TorchServe / ONNX)           (Evidently Monitor)
      │                              │                              │
      ▼                              ▼                              ▼
 [ TimescaleDB ]              csi-inference-result          [ Prometheus Exporter ]
 (재실/피처 시계열)                   │                              │
      │                    ┌─────────┴─────────┐                    ▼
      ▼                    ▼                   ▼           [ Prometheus / AlertManager ]
 [ Grafana ]        [ API · WebSocket ]  [ SMS / ntfy ]              │ Airflow Webhook
 (통합 관제)          (앱 실시간 알람)    (낙상 응급알림)              ▼
                                                            [ Airflow DAG ]
                                                       model_retrain_dag
```

> ## 현재 상태 (2026-09-29)
>
> **재실 경로가 관통한다.** 엣지 MQTT → 브리지 → Kafka → 인제스트 → TimescaleDB · 최신값 캐시
> → REST(`ResidentOut` 런타임 필드) · WebSocket(`/ws/live`)까지 실측 검증했다.
> 30초 재생에서 브리지 248건 전달·거부 0, `presence_samples` 118행 적재, 오류 0.
>
> **동작한다**: 서비스 API `/api/v1` 27경로 + `/ws/live` + JWT + 테넌시,
> PostgreSQL(Alembic `0002`, 테이블 11개) + TimescaleDB(`presence_samples`),
> 계약(`packages/contracts/`), 백엔드, 추론·segmentation, 엣지, 프론트엔드 회귀 검증은
> 루트의 `scripts/validate-repository.ps1`로 반복한다.
> AWS EC2 2대(db·api) 배포 — [deploy/aws/README.md](deploy/aws/README.md).
> **AWS 에는 아직 Kafka·MQTT 인스턴스가 없다**(M4). 설정이 없으면 인제스트는 꺼진 채로 뜨고
> CRUD 는 정상 동작한다.
>
> **현재 운영 후보 모델은 in-house temporal segmentation 모델이다**:
> `services/csi-fall-segmentation`의 S3 + PCA-ACF 피처와 Dual ResNet18 체크포인트를
> `services/inference`가 로드하고, MQTT `SignalMsg`의 `(960,30)` 진폭창을 500 samples로
> 리샘플해 64-bin 확률과 causal A/B trigger를 계산한다. 인제스트, WebSocket 캐시,
> `fall_events(source="EDGE")` 저장, 인증 REST API까지 실제 체크포인트로 소프트웨어 E2E를
> 통과했다. CUDA 학습·파인튜닝과 MLflow 모델 버전 등록도 실제 실행했다. CPU/RAM/GPU/VRAM/
> 디스크 실시간 모니터링과 AWS-neutral 용량 envelope를 포함한 결과는
> [docs/INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md](docs/INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md)에 있다.
> 전체 흐름과 검증 경계를 쉽게 설명한 공유용 문서는
> [docs/PIPELINE_STATUS_SIMPLE.md](docs/PIPELINE_STATUS_SIMPLE.md)를 참고한다.
>
> **실행 대상이 아닌 파일**: `services/api/.../main.py`는 구 로컬 백엔드의 REST 19종 + `/ws/live`
> 계약 원본으로 보존한 것이며, 엣지로 넘어간 모듈을 import하므로 import 자체가 불가능하다.
> `app.py`가 마운트하지 않는다. 운영 추론 진입점은 `python -m wifiguard_inference`다.
>
> 남은 작업은 [PORTING.md](PORTING.md) §2, 계획은 `~/.claude/plans/rosy-wobbling-balloon.md`.

---

## 책임 경계

| 하는 것 | 하지 않는 것 |
|---|---|
| MQTT 수신 → Kafka 라우팅 | CSI 수집·**재실감지** → 라즈베리파이 (네트워크와 무관하게 끊기지 않아야 함) |
| 시계열·관계형 영속 저장 | **학습 고정 규칙으로 서브캐리어 30개 선택·균일 리샘플** → 라즈베리파이 |
| **S3 + PCA-ACF 변환** — `(T,30)` 진폭창 → temporal segmentation 입력 | 펌웨어·하드웨어 제어 → ESP / Pi |
| **낙상 DL 추론 서빙** — 64-bin 확률 → exact-center 확률 → causal trigger | UI 렌더링 → 프론트엔드 |
| 낙상 상태 판정 · `fall_events` 생성 · 알림 라우팅 | |
| 서비스 API (REST + WS 팬아웃) · 인증 · 테넌시 | |
| 드리프트 감지 · 재학습 오케스트레이션 · 모델 레지스트리 | |

> temporal segmentation 모델 입력의 30채널은 Q-value 상위 30개가 아니다. 학습과
> 동일하게 245개 complex pair에서
> 0-based 121·122를 제외한 뒤 균등하게 30개를 고른다. Q-value는 선택된 30채널에서 CWT용
> PCA 대표신호를 구성하는 내부 단계에 사용한다. Pi는 이 규칙으로 리샘플한 `(T,30) float32`
> 진폭창을 보낸다. 320Hz·3초 기준 raw 115,200B, base64 약 153,600B이므로 다기기 운영 전에는
> 업링크·브로커 용량 시험이 필요하다.

---

## 구조

```
deploy/aws/         EC2 부팅 스크립트 · SSM · systemd — 1차 클라우드 토폴로지  [db·api 기동됨]
compose/            docker-compose.dev.yml (API·인제스트·선택 모델 포함) · obs.yml
infra/              Mosquitto · PostgreSQL · TimescaleDB · 관측 설정
monitoring/         검증 결과·AWS envelope 정적 용량 대시보드
services/
  api/              FastAPI 서비스 계층 — app.py · routers/ 9종 · auth/        [동작]
    .../realtime/   /ws/live 팬아웃 + GET /realtime/schema                     [동작]
    .../main.py     구 로컬 백엔드 REST 19종 + /ws/live 계약 원본       [참조 전용 · import 불가]
  ingest/           MQTT→Kafka 브리지 · 컨슈머 · 최신값 캐시 · WS 허브          [동작]
                    라이브러리다 — 실행 주체는 api 의 lifespan (워커 1 고정)
  model-serving/    체크포인트 로더·배치 추론 엔진                                [동작]
  csi-fall-segmentation/ S3+PCA-ACF·Dual ResNet18·학습/파인튜닝 패키지              [동작]
  inference/        Kafka 진폭창→segmentation 모델→InferenceResult                 [동작]
  notification/     ntfy 비동기 큐·백오프; 확정 FALL 뒤 수신자별 호출             [동작]
  mqtt-bridge/      → ingest 로 통합됨 (README 참조)
  drift/ provisioning/                                                         [비어 있음]
dags/               model_retrain_dag                                          [비어 있음]
packages/
  contracts/        ★ 4레포 스키마 SSOT                                        [동작]
    api.py          REST (camelCase) → openapi.json → 프론트 코드젠
    mqtt.py         presence · signal · telemetry · cmd · ack (snake_case)
    kafka.py        FeatureRecord · StatusRecord · InferenceResult
    realtime.py     /ws/live 프레임
    topics.py       MQTT·Kafka 토픽 조립·파싱
  db/               SQLAlchemy 2 모델 11개 + Alembic 0002                      [동작]
tools/              체크포인트·Kafka·MQTT·DB·API 검증 스크립트
docs/               명세 문서
_reference/         Window3BestModelInference (109MB) · dwt_analysis · collector-stride
```

## 기동 — 서비스 API (CRUD only)

```bash
uv sync                                  # uv workspace → 루트 .venv
docker start wg-pg                       # 로컬 PostgreSQL 16 (5432, wifiguard/devpass)
make migrate                             # alembic upgrade head
make seed                                # 목업 시드 (root@demo.io / member@demo.io / home@demo.io · 비밀번호 demo)
make api                                 # http://127.0.0.1:8000/docs
make test                                # pytest 77개 (wifiguard_test DB 자동 생성)
make openapi                             # openapi.json + realtime.schema.json → 프론트 코드젠 입력
bash tools/smoke.sh                      # curl 스모크
```

## 기동 — 실시간 경로까지 (재실 엔드투엔드)

인제스트는 **환경변수가 있을 때만** 켜진다. 없으면 조용히 꺼진 채로 뜨고 CRUD 는 정상이다.

```bash
docker start wg-pg wg-ts wg-kafka wg-mosq       # postgres · timescale · kafka · mosquitto

export DATABASE_URL="postgresql+psycopg://wifiguard:devpass@127.0.0.1:5432/wifiguard"
export TSDB_DSN="postgresql://wifiguard:devpass@127.0.0.1:5433/wifiguard_ts"
export MQTT_HOST=127.0.0.1 MQTT_PORT=1883 MQTT_TLS=0
export KAFKA_BOOTSTRAP=127.0.0.1:9092
export JWT_SECRET="개발용-32바이트-이상-비밀값"

uv run uvicorn wifiguard_api.app:app --port 8000 --workers 1   # ★ 워커 1 (아래 참조)
curl -s localhost:8000/health | jq .ingest                     # 브리지·컨슈머·싱크 실측 상태
```

기기를 하나 등록해 그 `mqttTopic` 의 tenant/device 를 엣지 `config/device.toml` 에 넣고
`python -m wifiguard_edge --transport replay` 를 돌리면 재실이 화면까지 흐른다.

통합 작업장 루트에서는 하드웨어 없이 실제 MQTT→Kafka 적재를 반복 검증할 수 있다.

```powershell
.\scripts\validate-mock-storage.ps1 -NetworkName wifiguard-dev_default
```

이 검증은 먼저 계약 메시지 3종을 직접 발행하고, 이어서 Raspberry 합성 replay를 구동한다.
각 실행은 새 UUID를 사용하며 Kafka에서 해당 레코드를 다시 읽어 Pydantic 계약으로 검증한다.

> **워커는 1개여야 한다.** `wifiguard_ingest` 는 라이브러리이고 실행 주체가 이 앱의
> lifespan 이다. Kafka 컨슈머와 최신값 캐시가 **이 프로세스의 인메모리 상태**라, 워커를
> 늘리면 각자 별도 컨슈머 그룹 멤버가 되어 메시지가 분산되고 캐시가 쪼개진다.
> 늘리려면 먼저 Redis pub/sub 을 도입해야 한다.

### 실시간 데이터가 흐르는 길

```
엣지 MQTT ──► mqtt_bridge ──► Kafka ──► consumers ──┬─► presence_sink ─► presence_samples (TSDB)
 (presence 4Hz                (토픽 검증)            ├─► telemetry_sink ─► devices.online
  signal 4Hz                                        └─► LiveCache ─┬─► REST ResidentOut 런타임 필드
  telemetry 1Hz)                                                   └─► LiveHub ─► /ws/live
```

브리지는 **페이로드의 신원 주장을 믿지 않는다** — 토픽에서 파싱한 `device_id`/`tenant_id` 가
정본이고, 페이로드가 다르게 주장하면 버린다. 그러지 않으면 자격증명이 샌 기기 하나가
다른 테넌트의 재실 이력을 위조할 수 있다.

구조: `packages/db`(모델 11개 + Alembic) · `packages/contracts`(계약 5종) ·
`services/api/{app.py, deps.py, auth/, routers/, realtime/}` · `services/ingest`(브리지·컨슈머·캐시·허브).
설계·결과는 `../REVIEW_20260907.md` §9 와 계획서 참조.

## 기동 — 로컬 E2E compose

```bash
cp compose/.env.example compose/.env    # 비밀번호를 실제 값으로 채울 것
make up                                  # API·브로커·DB 전 스택(모델 제외)
make model-up                            # temporal segmentation 체크포인트를 포함한 추론 E2E
make model-check                         # 체크포인트 형식·피처·합성 추론 검증
make model-e2e                           # 하드웨어 제외 소프트웨어 E2E 검증
make obs                                 # + 관측 스택
make ps / make logs / make down
```

기존 `make replay` 대상은 없으며, 대신 루트 `scripts/validate-mock-storage.ps1`을 사용한다.

compose 정적 해석과 컨테이너 기동을 검증했다. 실제 체크포인트로 MQTT→Kafka→피처→추론→
인제스트→PostgreSQL→인증 REST API 소프트웨어 E2E가 통과했다. CUDA 학습·파인튜닝·MLflow와
실시간 리소스 모니터링을 포함한 결과와 재현 방법은
[docs/INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md](docs/INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md)를 따른다.

---

## 모델 계약

운영 후보 체크포인트는 `services/csi-fall-segmentation`의 `S3 + PCA-ACF` temporal
segmentation 형식이다. 상세 파일·해시·입력 조건은
[docs/INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md](docs/INHOUSE_SEGMENTATION_E2E_AND_RESOURCES_KO.md)를 따른다.

```
와이어 입력: 3초 × 320Hz × 30채널 진폭창, float32
리샘플: 500 samples @ 166.6667Hz
입력 A: CWT S3 이미지      (224, 224)
입력 B: PCA-ACF 맵         (1, 128, 64)
모델:   독립 ResNet-18 두 개 → temporal fusion → 64 bins
출력:   sigmoid 64-bin probability → exact-center interpolation
후처리: causal trigger B (A/B 비교 가능), fixed delay 1.5초
임계값: 0.5
```

제공된 77개 녹화는 모델 학습에 사용됐으므로 이번 실행은 구현 parity와 파이프라인 작동을 검증한다.
독립 데이터 일반화나 의료기기 성능을 뜻하지 않는다.

## 세 축의 임계값 — 절대 섞지 말 것

| 값 | 출처 | 척도 | UI 라벨 |
|---|---|---|---|
| `threshold` = **0.5** | segmentation 체크포인트 | **확률** 0~1 | **"판정 임계값"** / "낙상 확률 임계값" |
| `presence_mv_threshold` | 캘리브레이션 (**Pi**) | MV 스케일, 기본 2.0 | "움직임 임계값" |
| `wander_baseline` | 캘리브레이션 (**Pi**) | Welch PSD 스케일, 기본 0.5 | "재실 baseline" |

## 지연 예산

GPU steady-state benchmark에서 피처 중앙값 **34.535ms/window**, P95 **37.242ms/window**,
모델 추론 중앙값 **2.720ms/window**를 관측했다. 첫 4-window 피처 배치는 CUDA/JIT warm-up을
포함해 **5,479ms**였으므로 서비스 시작 시 warm-up이 필요하다. 이 값은 단일 개발 PC의 bounded
측정이며 운영 SLA가 아니다. 다기기 처리량·MQTT 대역폭·장시간 soak는 별도 검증 대상이다.

---

## 문서

- [PORTING.md](PORTING.md) — 이식 매핑 · 남은 작업 · 지켜야 할 계약 · 대용량 자산 방침
- [docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) — 이 레포의 명세. **§7이 4레포 공통 계약 SSOT다**
- [docs/CSI-Guard_인프라구축.md](docs/CSI-Guard_인프라구축.md) — 전체 아키텍처, 시나리오 A/B/C
- [docs/CSI-Guard_완성목표_실행계획_v1.0_20260729.md](docs/CSI-Guard_완성목표_실행계획_v1.0_20260729.md) — G1~G7 실행 계획
- [docs/FACILITY 구상도.md](docs/FACILITY%20구상도.md) — 5계층 · 규모 산정 · 온프레미스 옵션
- [docs/CSI-Guard_데이터모델_ERD_v1.0.docx](docs/CSI-Guard_데이터모델_ERD_v1.0.docx) — 후보 ERD
