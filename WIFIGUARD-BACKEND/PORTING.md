# PORTING — WIFIGUARD-BACKEND

이 레포는 **아직 실행되지 않는다.** 이식 원본을 목표 경로에 배치하고 미들웨어 compose 뼈대를 만든 상태다.

> **이식본은 이 상태로 import 되지 않는다.** 의도된 것이다 — 아래 §1-2 참조. 링버퍼·피처 의존 제거는 실제 이식 작업(Phase 3·4)이지 배치 단계의 일이 아니다.

- 명세: [docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md)
- **이 명세 §7이 4개 레포 공통 계약의 SSOT다.**
- 원본 경로 기준: `GAA/` = `csi_fall/MVP-MOCKUP/Guardian Angel Alert/`
- 배치 작업일: 2026-09-03

---

## 1. 이식 매핑

### 1-1. 거의 무변경으로 살릴 수 있는 것

| 원본 | 줄 | 목표 | 변경 필요 |
|---|---:|---|---|
| `GAA/backend/inference/model.py` | 127 | `services/model-serving/src/wifiguard_serving/model.py` | **무변경.** `DualBranchResNet` 정의는 체크포인트 `state_dict` 키와 1:1이라 건드리면 로드가 깨진다 |
| `GAA/backend/inference/engine.py` | 94 | `services/model-serving/src/wifiguard_serving/engine.py` | 체크포인트 경로를 **MLflow Registry + S3**로 / Windows `PosixPath` 몽키패치 **제거 가능**(Linux 컨테이너 전용) / 디바이스 선택 `cuda→mps→cpu`를 `cuda→cpu`로 / `warmup()`은 **유지**(첫 요청 지연 제거) |
| `GAA/backend/notifier.py` | 165 | `services/notification/src/wifiguard_notify/adapters/ntfy.py` | 스레드+큐(최대 32건)·재시도(최대 3회, 백오프 1s/2s) **구조 유지**. 발송 지연이 감지 루프를 막지 않게 하는 것이 이 구조의 목적이다 |

### 1-2. 이식했으나 지금은 깨져 있는 것 (의도)

| 원본 | 줄 | 목표 | 왜 깨져 있나 |
|---|---:|---|---|
| `GAA/backend/main.py` | 461 | `services/api/src/wifiguard_api/main.py` | `from csi.buffer import`, `from csi.serial_reader import`, `from presence import`, `from presence_loop import`, `from onboarding import` — **전부 엣지로 넘어간 모듈이다.** REST 19종 + WS 1종의 **계약 원본**으로 보존한 것이며, `routers/`로 분해하면서 DB·MQTT 연동으로 다시 쓴다 |
| `GAA/backend/detector.py` | 229 | `services/inference/src/wifiguard_inference/state_machine.py` | `from csi.buffer import RingBuffer`, `from features import ...` — 엣지 모듈. 명세 §6.1: **링버퍼 의존을 제거하고 "확률 시퀀스 → 상태"로 축소**해야 한다 |

### 1-3. 판정 로직 — 축소 시 반드시 보존할 것

`state_machine.py`에서 살려야 하는 핵심 (명세 §4 Phase 3-2):

```
STRIDE_SEC        = 0.25
DEFAULT_THRESHOLD = 0.468
MODE_SIZE         = 5
COOLDOWN_SECONDS  = 10.0

raw_pred = int(proba >= threshold)
recent   = deque(maxlen=5)
majority = int(sum(recent)*2 > len(recent))   # deque가 5개 찼을 때만, 아니면 0

COOLDOWN 중이면 전이 차단 (cooldown_until)
majority → FALL (fall_count++, 알림 발행)
FALL 이탈 → COOLDOWN 10초
그 외 → raw면 SUSPECT, 아니면 IDLE
```

> **인과(causal) 다수결이다.** 오프라인 검증에 쓴 `mode5`는 중심 윈도우 기준이라 미래 윈도우 2개가 필요해 실시간에 쓸 수 없다. 이 대체가 원안과 동등한 성능인지는 **연구단 확인 대기 중인 오픈 아이템**이다 (명세 §9-2, R4). 참조 구현: `tools/infer_validation.py`의 `moving_mode(size=5)`.

### 1-4. 참조 자산 (`_reference/`)

| 경로 | 크기 | 원본 | 용도 |
|---|---:|---|---|
| `_reference/Window3BestModelInference/` | **109MB** | `GAA/Window3BestModelInference/` (gitignored) | 모델 계약·검증 성능의 원본 |
| ↳ `weights/best_model.pt` | **89MB** | | 현행 기본 체크포인트. sha256 `b141020e…9fc11`, epoch 5 |
| ↳ `scripts/train_losnlos_resnet18_s3_acf_dual_end2end.py` | 29KB | | **`dags/tasks/adapt.py`의 원본.** SAM 옵티마이저·HuberizedCE·WeightedRandomSampler 포함 |
| ↳ `scripts/evaluate_feature_pair_domain_robustness.py` | 25KB | | **`dags/tasks/validate.py`(Validation Gate)의 원본** |
| ↳ `PACKAGE_MANIFEST.json`, `README_KO.md` | | | 모델 계약·검증 성능 표 |
| `tools/infer_validation.py` | 11KB | 위 패키지 | mode5 후처리 참조 |
| `_reference/dwt_analysis/` | 1.1MB | `csi_fall/dwt_analysis/` (git repo) | **실제로 동작하는 유일한 DVC 파이프라인.** `dvc.yaml`(preprocess·featurize 2단계), `dvc.lock`, `config/`, `src/dwt_coef/`, `scripts/`, 루트 `.md` 12종 |
| `_reference/collector-stride/` | 37KB | `csi_fall/esp32c5/tools/stride/` | 다기기 CSI 수집 도구 (데이터 수집 세션용). 명세 §6.1의 `tools/collector/` 후보 |
| `_reference/backend_README_현행.md` | 12KB | `GAA/backend/README.md` | 현행 파이프라인 실행법·계약 |
| `_reference/backend_requirements_현행.txt` | 1KB | `GAA/backend/requirements.txt` | 현행 의존성 8개 |

**`dwt_analysis` 복사 범위**: 코드 자산만 가져왔다 (`dvc.yaml`, `dvc.lock`, `config/`, `src/`, `scripts/`, 루트 `.md`). `Mendeley/`(환경별·피험자별 CSV 수천 개), `data/`, `results/`, `notebooks/`, `temp/`는 수 GB라 제외했다. 원본은 `csi_fall/dwt_analysis/`에 그대로 있고 자체 git 레포다.

**복사하지 않은 대용량**: `csi_fall/ValidationInferencePackage.zip` (**6.79GB**) — 어느 명세의 이식 매핑에도 없다. 필요해지면 원본 경로에서 가져올 것.

### 1-5. 복사하지 않은 것 (명세 §6.2)

| 원본 | 이유 |
|---|---|
| `GAA/backend/{presence/,features/,csi/,onboarding.py,presence_loop.py,bench_pipeline.py}` | **엣지(Pi) 소관** → WIFIGUARD-RASPBERRY |
| `csi_fall/csi_fall_monorepo/` | 파이썬 본문이 **100% `NotImplementedError` 스텁**이다. `pyproject.toml`의 workspace 멤버가 `apps/infrastructure`인데 디스크에는 `apps/infra`라 `uv sync`도 깨져 있다. 다만 uv workspace + PEP-420 레이아웃과 `ARCHITECTURE.md`/`DVC_GUIDE.md`의 문서 구성은 참고할 만하다 — **반면교사 겸 참고** |
| `csi_fall/infra/raspberry/fall_detector.py` | 규칙 기반 낙상 감지. DL 모델과 무관한 별개 계보 |
| `GAA/reference/fall_detect/` | 구 MV 임계값 파이프라인 (gitignored, 로컬 전용). 낙상 절반(`fall_state_machine.py`)은 이식하지 않는다 — 여기서는 DL 모델을 쓴다 |

---

## 2. 아직 없는 것 = 작업 목록

명세 §2의 F-B01~F-B20. **신규 파이썬 모듈은 의도적으로 만들지 않았다** — `csi_fall_monorepo`가 스텁만 남기고 멈춘 전례를 반복하지 않기 위해서다.

### Phase 0 — 계약 고정 (★ 최우선)

| 목표 경로 | ID | 내용 |
|---|---|---|
| `packages/contracts/src/wifiguard_contracts/mqtt.py` | F-B20 | `PresenceMsg` · `TelemetryMsg` · `WindowMsg` · `CmdMsg` · `AckMsg` (Pydantic v2) |
| `packages/contracts/src/wifiguard_contracts/kafka.py` | | `FeatureRecord` · `InferenceResult` |
| `packages/contracts/src/wifiguard_contracts/api.py` | | REST/WS 스키마 → OpenAPI → **프론트 타입 코드젠** |
| `infra/mosquitto/mosquitto.conf`, `acl` | | 로컬 개발 브로커 (compose가 참조하는데 파일이 없다) |
| `infra/kafka/` | F-B02 | 토픽 정의, 파티션·보존 정책 |
| `tools/replay_edge.py` | | 엣지 페이로드 재생 (Pi 없이 개발) |

> **4개 레포가 이 스키마 하나를 참조해야 이후 병렬 개발이 가능하다.** 여기가 먼저다.

### 2026-09-08 구현 완료 (HOME/FACILITY 서비스 계층 — 실시간·MLOps 제외)

| 목표 경로 | ID | 상태 |
|---|---|---|
| `packages/db/src/wifiguard_db/{models,migrations/versions/0001_initial.py}` | F-B11 | **완료** — 테이블 11개, XOR 스코프, 주 장치 부분 유니크 |
| `packages/contracts/src/wifiguard_contracts/api.py` → `openapi.json` | F-B20(REST 부분) | **완료** — MQTT/Kafka 스키마는 미착수 |
| `services/api/src/wifiguard_api/{app.py, deps.py, auth/, routers/*}` | F-B08(신규 CRUD)·F-B10 | **완료** — `/api/v1` 27 경로. 현행 19종(main.py)은 계약 원본으로 보존, 미구현 |
| `services/api/tests/` (pytest 32) · `tools/{seed.py, export_openapi.py, smoke.sh}` | R6 | **완료** |

### Phase 1~6 (남은 것)

| 목표 경로 | ID | 내용 | Phase |
|---|---|---|---|
| `infra/timescaledb/init.sql`, `infra/postgres/init.sql` | F-B03 | hypertable · retention · 연속집계 | 1 |
| `services/ingest/src/wifiguard_ingest/consumer.py` | F-B03 | aiokafka → TimescaleDB 배치 삽입 | 2 |
| `services/inference/src/wifiguard_inference/consumer.py` | F-B04 | Kafka → 서빙 호출 → result topic | 3 |
| `services/inference/src/wifiguard_inference/decoder.py` | | 역양자화 + zstd 해제 (**엣지 codec의 짝**) | 3 |
| `services/inference/src/wifiguard_inference/hot_reload.py` | F-B17 | MLflow 신 버전 로드 | 6 |
| `services/model-serving/src/wifiguard_serving/handler.py` | F-B05 | TorchServe 핸들러 / ONNX 러너 | 3 |
| `services/api/src/wifiguard_api/routers/` | F-B08 | main.py 분해 — monitor · devices · residents · falls · calibration · config · notify · ws | 4 |
| `services/api/src/wifiguard_api/auth/` | F-B10 | JWT · RBAC · 테넌시 의존성 | 4 |
| `services/api/src/wifiguard_api/realtime/` | F-B09 | Redis pub/sub → WS 팬아웃 | 4 |
| `services/notification/src/wifiguard_notify/router.py` | F-B14 | 수신자별 채널 라우팅 (채널 실패 격리) | 4 |
| `services/notification/.../escalation.py` | F-B14 | FACILITY 무응답 에스컬레이션 (1차→2차→3차) | 4 |
| `services/notification/.../audit.py` | F-B14 | 감사 이력 — **법적 증빙 가능해야 함(변조 방지)** | 4 |
| `services/notification/.../adapters/sms.py` | F-B12 | NHN Toast / 알리고 / Twilio (미정) | 4 |
| `services/notification/.../adapters/ars.py` | | 스텁 | 4 |
| `services/mqtt-bridge/` | F-B01 | 로컬 개발: Mosquitto → Kafka (운영은 IoT Rule이 대체) | 0 |
| `services/provisioning/` | F-B15 | 기기 등록 · 인증서 발급/회수 | 4 |
| `services/drift/src/wifiguard_drift/{monitor,exporter,baseline}.py` | F-B07 | Evidently (KS-Test, PSI) + Prometheus Exporter | 5 |
| `dags/model_retrain_dag.py`, `dags/tasks/` | F-B16 | extract → adapt → **validate(게이트)** → register → deploy | 6 |
| `infra/{prometheus,alertmanager,grafana,loki}/` 설정 | F-B18 | obs compose가 참조하는데 파일이 없다 | 5 |
| `tools/seed.py` | | 개발용 시드 데이터 | 1 |

---

## 3. 반드시 지켜야 할 계약

### `/ws/live` — 재실과 낙상의 독립성

재실 필드(`presence_state`, `mv_current`, `wander_current` …)는 **엣지가 연결되어 있으면 항상** 존재하고, 낙상 필드(`proba_fall`, `detect_state` …)는 **모델이 로드된 경우에만** 추가된다.

> **낙상 필드의 부재는 "낙상 없음"이 아니라 "낙상 감지가 동작하지 않음"이다.** 프론트가 두 상태를 반드시 구별해야 한다 (명세 frontend R6 — 안전 사고 위험).

### 알림 이중 경로 — 유지할 것

```
휴대폰 푸시:  추론 → notifier → ntfy/SMS → 단말        (HTTP·WS를 거치지 않는다)
앱 내 알람:   /ws/live 의 detect_state 구독 → FallAlarmModal   (완전히 별개 경로)
```

두 경로는 "상태머신이 FALL로 전이한다"는 **트리거만** 공유한다. **이 이중화가 한 쪽이 죽어도 알림이 전달되게 하는 안전망이다.**

### 가용성 — 무증상 중단 금지

클라우드 장애·네트워크 단절 시 재실감지는 엣지에서 **유지**되고 낙상 감지는 **중단**된다. 안전 기능이므로 **이 상태를 사용자에게 명확히 표시해야 한다.**

### 학습 데이터 원칙 — 문서 간 충돌 해소됨

`인프라구축.md` 시나리오 C는 사용자의 "오탐지" 응답을 Negative Sample로 재학습에 쓴다고 기술하지만, **G4 원칙을 채택한다**: 사용자·보호자 응답(확인/출동/오탐지)은 **학습에 반영하지 않고** 운영 지표·사후 분석 전용으로만 보존한다. 응답의 신뢰도를 검증할 방법이 없기 때문이다. 바꾸려면 **응답 신뢰도 검증 절차를 먼저 설계**해야 한다.

---

## 4. 대용량 자산 처리 방침

| 자산 | 크기 | 현재 | 분리 시 |
|---|---:|---|---|
| `weights/best_model.pt` | **89MB** | `_reference/`에 사본 | **GitHub 100MB 하드 한도에 근접.** Git LFS 또는 MLflow Registry + S3 필수. `.gitignore`에 `*.pt`를 이미 넣어 두었다 |
| `_reference/Window3BestModelInference/dataset/features/*.npz` | 19MB | 사본 | 검증 데이터 — DVC 또는 오브젝트 스토리지 |
| `_reference/dwt_analysis/` | 1.1MB (코드만) | 사본 | 원본은 자체 git 레포. **서브모듈로 참조**하는 편이 나을 수 있다 |
| `csi_fall/ValidationInferencePackage.zip` | 6.79GB | 복사 안 함 | 필요 시 원본 경로에서 |

---

## 5. 알아둘 것

- **원본을 지우지 않았다.** `GAA/backend/`는 지금도 로컬에서 구동되는 현행 코드이며, 이 레포는 사본이다.
- **MLflow는 미도입 상태다.** 이 프로젝트 어디에서도 실제로 쓰인 적이 없고, `apps/research/*/train.py`에 `MLFLOW_TRACKING_URI` 환경변수를 읽는 죽은 필드가 하나 있을 뿐이다. 실제 실험 기록은 **DVC params + `training_summary.json` + git tag**로 이루어진다. **완전한 신규 작업으로 계획할 것** (명세 R7).
- **신규 도입 스택이 5개다** (Kafka · Airflow · MLflow · Evidently · Prometheus). 명세 R1: **Phase 순서 엄수.** G1(저장) → 추론 → 알림까지가 최소 동작 세트이고, MLOps 루프(Phase 5·6)는 뒤로.
- **깨진 문서 링크**: `_reference/backend_README_현행.md`가 참조하는 `reference/fall_detect/migration.md`·`occupation_pipline.md`·`ACF_Scalogram_FeatureExtraction/README_KO.md`는 gitignored 로컬 전용이고, `docs/작업명세_로컬_실시간_낙상감지_v1.0.md`(README에는 `dcos/`로 오타)는 **삭제되었으나 커밋되지 않은 상태**다.
- **테스트 러너 없음.** 명세 R6은 pytest 도입을 Phase 0에 포함할 것을 권고한다. 별도 합의 사항 (§9-6).
- **선행 미결정** (명세 §9): 클라우드 사업자·리전·**비용 상한** ← G1 착수의 선행 조건. 인과 다수결 ↔ mode5 동등성. 모델 서빙 스택(TorchServe / Triton / FastAPI+ONNX). 자동 재적응 허용 범위(초기에는 **승인 방식 권장**).
