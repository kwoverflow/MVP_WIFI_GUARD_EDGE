# PORTING — WIFIGUARD-RASPBERRY

이 레포는 **아직 실행되지 않는다.** 이식 원본을 목표 경로에 배치한 상태이며, 엔트리포인트(`__main__.py`)와 신규 모듈이 비어 있다.

- 명세: [docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md)
- 계약 SSOT: [docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) §7
- 원본 경로 기준: `csi_fall/` (= `c:/Users/jspar/Desktop/SCHOOL/csi_fall/`)
- 배치 작업일: 2026-09-03

---

## 1. 이식 매핑

`GAA/` = `csi_fall/MVP-MOCKUP/Guardian Angel Alert/`

### 1-1. 무변경 이식 — 그대로 동작함

패키지 내부가 전부 상대 import(`from .protocol import ...`)라 경로를 옮겨도 깨지지 않는다.

| 원본 | 줄 | 목표 | 상태 |
|---|---:|---|---|
| `GAA/backend/presence/__init__.py` | 19 | `src/wifiguard_edge/presence/__init__.py` | 무변경 |
| `GAA/backend/presence/config.py` | 53 | `src/wifiguard_edge/presence/config.py` | 무변경 |
| `GAA/backend/presence/preprocessing.py` | 130 | `src/wifiguard_edge/presence/preprocessing.py` | 무변경 |
| `GAA/backend/presence/streaming_features.py` | 180 | `src/wifiguard_edge/presence/streaming_features.py` | 무변경 |
| `GAA/backend/presence/state_machine.py` | 161 | `src/wifiguard_edge/presence/state_machine.py` | 무변경 |
| `GAA/backend/features/__init__.py` | 3 | `src/wifiguard_edge/features/__init__.py` | 무변경 |
| `GAA/backend/features/realtime.py` | 163 | `src/wifiguard_edge/features/realtime.py` | 무변경 (계약 고정) |
| `GAA/backend/features/common.py` | 275 | `src/wifiguard_edge/features/common.py` | 무변경 |
| `GAA/backend/features/acf.py` | 82 | `src/wifiguard_edge/features/acf.py` | 무변경 |
| `GAA/backend/csi/__init__.py` | 0 | `src/wifiguard_edge/csi/__init__.py` | 무변경 |
| `GAA/backend/csi/buffer.py` | 108 | `src/wifiguard_edge/csi/buffer.py` | 무변경 |
| `GAA/backend/csi/protocol.py` | 115 | `src/wifiguard_edge/csi/protocol.py` | 무변경 (명세 이탈 — §1-4) |
| `GAA/backend/csi/serial_reader.py` | 152 | `src/wifiguard_edge/csi/serial_reader.py` | 무변경 (명세 이탈 — §1-4) |
| `csi_fall/infra/raspberry/protocol.py` | 333 | `src/wifiguard_edge/transport/protocol.py` | 파일 이동만 |
| `csi_fall/infra/raspberry/test_protocol.py` | 90 | `tests/test_protocol.py` | 파일 이동만 |

### 1-2. import 접두사만 수정 — 이번 배치에서 적용 완료

`backend/`는 평평한 디렉토리라 최상위 모듈이 절대 import(`from presence import ...`)를 썼다. `src/wifiguard_edge/` 패키지 안으로 들어가면서 상대 import로 바꿨다. **이것이 이번 배치에서 가한 유일한 코드 수정이다.**

| 목표 파일 | 수정 |
|---|---|
| `src/wifiguard_edge/presence_loop.py` | `from csi.buffer import` → `from .csi.buffer import`<br>`from presence import` → `from .presence import` |
| `src/wifiguard_edge/calibration/onboarding.py` | `from presence import` → `from ..presence import` |
| `src/wifiguard_edge/transport/spi_reader.py` | `import protocol` → `from . import protocol` |
| `src/wifiguard_edge/transport/spi_mock.py` | `import protocol` → `from . import protocol` |

원본(`GAA/backend/presence_loop.py` 183줄, `GAA/backend/onboarding.py` 317줄, `csi_fall/infra/raspberry/spi_interface.py` 279줄, `spi_interface_windows.py` 432줄)과의 차이는 이 줄들뿐이다. `diff`로 확인할 수 있다.

### 1-3. 이식했으나 손봐야 하는 것

| 파일 | 문제 |
|---|---|
| `tools/bench_pipeline.py` | ~~`from inference import FallInferenceEngine` — **엣지에 `inference/`가 없다.**~~ 그리고 `from features import …`(평평한 절대 import)도 이식 후 깨져 있었다(REVIEW P2·P9). **2026-09-08 수정 완료**: `from wifiguard_edge.features import FeatureConfig, extract_window_features`(미설치 시 `src/` 를 sys.path 에 추가), 추론 import·측정 구간·`--device` 제거, `total = feature_ms`. 명세 §4 Phase 3-1이 요구하는 Pi 기준선 측정의 출발점이다. |
| `tests/test_protocol.py` | `import protocol` → `from wifiguard_edge.transport import protocol` (REVIEW P1, **2026-09-08 수정 완료**). `pyproject.toml` 의 `pythonpath = ["src"]` 로 수집된다. |
| `src/wifiguard_edge/transport/spi_reader.py` | 모듈 최상단 `import spidev` / `import RPi.GPIO` — 개발 PC에서는 import 자체가 실패한다. `transport/base.py` 추상화 도입 시 지연 import 또는 팩토리로 정리할 것. |
| `src/wifiguard_edge/transport/spi_reader.py` | 명세 §5.1: `SerialReader` 덕타이핑 계약(`running` / `packet_count` / `send_line()` / `get_window()`)을 **아직 구현하지 않는다.** 시제품은 deque 기반의 다른 인터페이스다. 이걸 맞추는 것이 Phase 1의 핵심이다 — 맞추면 상위 파이프라인 전부를 무수정 재사용할 수 있다. |
| `src/wifiguard_edge/transport/protocol.py` | 시제품 기준(`SPI_FRAME_SIZE_LARGE=4096`, `CSI_MAX_LEN=128`, `csi_len: uint8`, 프레임 140B). 명세 §4 Phase 0은 **8192 / 612 / uint16 / 626B**로 확대할 것을 요구한다. ESP 레포와 동시 진행 항목. |
| `src/wifiguard_edge/calibration/onboarding.py` | `silence_confirm_s = 0.2`는 UART 921600 baud 드레인(~7ms) 기준이다. **SPI 전환 시 재산정 필요** (명세 §9-3). |
| `src/wifiguard_edge/presence/state_machine.py` | `PresenceDetector.__init__` 기본값(2.5 / 2.0 / 6.0)이 `PresenceConfig` 기본값(2.0 / 1.8 / 10.0)과 다르다. 런타임에는 매 틱 `set_thresholds()`가 덮어써서 `PresenceConfig`가 이기지만, 명세 §4 Phase 1은 혼동 방지를 위해 일치시킬 것을 권고한다. |
| `src/wifiguard_edge/presence/config.py` | 세 곳(캘리브레이션 executor 스레드 / 설정 갱신 / presence 스레드)에서 접근하는데 **락이 없다.** 락 또는 불변 스냅샷 교체로 정리 권장 (명세 R8). |

### 1-4. 명세에서 의도적으로 벗어난 부분

명세 §3은 `csi/`에 `buffer.py`만 두고 전송은 전부 `transport/`로 보낸다. 여기서는 `csi/protocol.py`(UART 프레임 파서, magic 0xA55A)와 `csi/serial_reader.py`도 함께 남겼다.

**이유**: `transport/base.py` 추상화가 들어오기 전까지 **현행 UART 경로를 개발용 폴백으로 유지**하기 위해서다. 지금 실기 SPI 하드웨어 없이 돌릴 수 있는 유일한 실데이터 경로이고, 현행 `backend/`와 동일 동작을 대조할 기준이기도 하다. `transport/base.py`가 생기면 `csi/serial_reader.py`를 `transport/serial_reader.py`로 옮기고 이 예외를 없앤다.

이름 충돌 주의: `csi/protocol.py`(UART)와 `transport/protocol.py`(SPI)는 **서로 다른 프로토콜**이다.

### 1-5. 참조만 — 승계하지 않음 (명세 §6.2·§6.3)

| 원본 | 배치 | 이유 |
|---|---|---|
| `csi_fall/infra/raspberry/state_manager.py` (277줄) | `_reference/state_manager.py` | IDLE/TRAINING/OCCUPANCY/FALL_DETECTING 모드 관리 **개념만** 참조 |
| `csi_fall/infra/pipline.md` (361줄) | `_reference/pipline.md` | SPI 시제품의 시나리오별 데이터 흐름 (진단 목적) |
| `csi_fall/infra/raspberry/SETUP_BY_PLATFORM.md` | `deploy/SETUP_BY_PLATFORM.md` | `install.sh`의 근거 |

### 1-6. 복사하지 않은 것

| 원본 | 이유 |
|---|---|
| `csi_fall/infra/raspberry/fall_detector.py` (312줄) | LPF + mean/std/peak_diff + burst∧stillness 규칙. **S3+PCA-ACF DL 모델과 무관한 별개 계보.** 낙상 판정은 클라우드가 한다 |
| `csi_fall/infra/raspberry/web_server.py` (479줄) | 프로토타입 인증(평문 비밀번호, 프로세스마다 재생성되는 `SECRET_KEY`, 어디에도 걸리지 않은 `verify_token`), deprecated `@app.on_event`, 스레드-asyncio 혼용 버그. 로컬 UI는 프론트엔드 레포가 대체 |
| `csi_fall/infra/raspberry/{main.py,static/}` | 위와 한 몸 |
| `csi_fall/infra/raspberry/.conda/` | 벤더링된 conda 환경 105MB |
| `csi_fall/infra/raspberry/fall_detection.log` | 로그 250KB |
| `csi_fall/csi_fall_monorepo/apps/infra/raspberry/` | `infra/raspberry/`의 **구버전** 사본이다(`web_server.py` 415줄 vs 479줄, `spi_interface_windows.py`·`SETUP_BY_PLATFORM.md` 없음). 신버전을 원본으로 삼았다 |
| `GAA/backend/{main,detector,notifier}.py`, `GAA/backend/inference/` | 클라우드 소관 → WIFIGUARD-BACKEND |

> **재실 신호체인의 원조**: `GAA/reference/fall_detect/` (= `csi_fall/esp32c5/tools/fall_detect/`). `backend/presence/`와 `onboarding.py`가 여기서 이식된 것이다. gitignored 로컬 전용이라 복사하지 않았다. `migration.md`·`occupation_pipline.md`에 포팅 근거가 있다.

---

## 2. 아직 없는 것 = 작업 목록

명세 §2의 F-P11~F-P16 및 §3 트리에서 ★로 표시된 신규 모듈. **의도적으로 빈 채로 두었다** — `csi_fall_monorepo`가 `NotImplementedError` 스텁만 남기고 멈춘 전례를 반복하지 않기 위해서다.

| 목표 경로 | ID | 내용 | 명세 |
|---|---|---|---|
| `src/wifiguard_edge/__main__.py` | — | 엔트리포인트 · 스레드 기동/정지 | §3 |
| `src/wifiguard_edge/config.py` | — | `config/*.toml` 로딩 · 런타임 갱신 | §3 |
| `src/wifiguard_edge/transport/base.py` | — | Transport 프로토콜 정의 (Protocol/ABC) | §5.1 |
| `src/wifiguard_edge/feature_loop.py` | — | FeatureLoop 스레드 (게이팅 연동, 0.25s) | §3 |
| `src/wifiguard_edge/gating.py` | **F-P11** | 업로드 게이트 정책 — **클라우드 비용과 대역폭을 결정한다** | §4-1, §5.4 |
| `src/wifiguard_edge/mqtt/publisher.py` | **F-P12** | paho-mqtt + mTLS, 아웃바운드 8883 | §4-3 |
| `src/wifiguard_edge/mqtt/command.py` | **F-P13** | `cmd` 구독 → 처리 → `ack` 발행 | §4-4 |
| `src/wifiguard_edge/mqtt/codec.py` | **F-P14** | MessagePack + int8 양자화 + zstd | §5.5 |
| `src/wifiguard_edge/spool/sqlite_spool.py` | **F-P15** | 단절 구간 버퍼 | §5-1 |
| `src/wifiguard_edge/health.py` | — | 헬스 메트릭 수집 | §3 |
| `deploy/*` | **F-P16** | systemd + 워치독 — **뼈대는 작성됨, 실기 미검증** | §5-2 |
| `tools/replay.py` | — | 저장된 CSI로 파이프라인 재현 | §3 |

### 선행 조건 (다른 것보다 먼저)

1. **`tools/bench_pipeline.py`를 Pi에서 돌려 기준선 확보** (§4 Phase 3-1). 이 수치가 나오기 전에는 최적화 목표를 세울 수 없다. 목표: `total_ms` p90 < 250ms.
2. **`freq_to_scale` 캐시가 살아 있는지 확인** — 호출당 약 0.5초다. `fs_quantize_hz = 0.25` 양자화가 캐시를 적중시킨다. 이게 빠지면 즉시 예산 초과 (R3).
3. **`ssqueezepy` ARM64 휠 확인** — 없으면 `features/common.py`의 `fallback_cwt`(NumPy 전용 6-cycle complex Morlet) 경로로 가고, 두 경로의 출력 동등성을 검증해야 한다 (R2).
4. **`vertical_denoise`/`horizontal_denoise` 벡터화** — 224×224 격자 위 중첩 파이썬 루프를 초당 4회 돈다. Pi에서 가장 유력한 예산 초과 지점 (R1).

---

## 3. 검증 상태

이번 배치에서 확인한 것 (Python 3.13.2):

```bash
cd migration/WIFIGUARD-RASPBERRY
PYTHONPATH=src python -c "from wifiguard_edge.presence import PresenceConfig, PresenceDetector, compute_final_signal"   # OK
PYTHONPATH=src python -c "from wifiguard_edge.features import FeatureConfig, extract_window_features"                    # OK
PYTHONPATH=src python -c "from wifiguard_edge.csi.buffer import RingBuffer"                                              # OK
PYTHONPATH=src python -c "import wifiguard_edge.presence_loop"                                                           # OK
PYTHONPATH=src python -c "from wifiguard_edge.calibration.onboarding import run_calibration"                             # OK
```

**미검증**: `transport/*`(spidev/RPi.GPIO 필요), `tests/test_protocol.py`, `deploy/install.sh`, `config/*.toml`(읽는 코드 없음), 실기 동작 전체.

---

## 4. 알아둘 것

- **원본을 지우지 않았다.** `GAA/backend/`는 지금도 로컬에서 구동되는 현행 코드이며, 이 레포는 사본이다. 양쪽이 갈라지기 시작하면 어느 쪽이 정본인지 명시할 것.
- **`csi_fall/csi_fall_monorepo/`는 반면교사다.** 파이썬 본문이 100% `NotImplementedError` 스텁이고, `pyproject.toml`의 workspace 멤버가 `apps/infrastructure`인데 디스크에는 `apps/infra`라 `uv sync`가 깨져 있다. 다만 PEP-420 네임스페이스 레이아웃과 `ARCHITECTURE.md`/`DVC_GUIDE.md`의 문서 구성은 참고할 만하다.
- **테스트 러너 없음.** `tests/test_protocol.py`는 pytest가 아니라 `__main__` 스크립트다. `pyproject.toml`에 pytest 설정만 미리 넣어 두었다. 도입은 별도 합의 사항 (명세 backend §9-6).
