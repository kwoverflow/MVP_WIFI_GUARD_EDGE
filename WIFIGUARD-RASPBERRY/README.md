# WIFI-GUARD Edge (라즈베리파이)

CSI를 SPI로 받아 **재실을 판정하고**, 활동 구간의 **피처를 추출해 클라우드로 올린다.**

```
[ESP32-C5 RX] ──WGSP v1 Batch8 / SPI 6MHz──► [Raspberry Pi]
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              PresenceLoop        FeatureLoop        Calibration
              (상시, 0.25s)      (게이팅 시, 0.25s)   (61초 4단계)
                    │                  │                  │
                    └──────────────────┴──────────────────┘
                                       ▼
                          MQTT over TLS 8883 (아웃바운드 전용)
                                       ▼
                      [AWS IoT Core] ──IoT Rule──► [Kafka]
```

> ## 현재 상태 (2026-09-10): Batch8 SPI부터 브로커까지 실측 동작한다.
>
> `python -m wifiguard_edge` 로 기동한다. 수집 → 재실감지 → 게이팅 → 대표신호 → MQTT 발행이
> 이어져 있고, 로컬 Mosquitto 로 실측 검증했다(presence 4Hz · signal 4Hz · telemetry 1Hz,
> 오류 0). Pi·수신기 없이도 `--transport replay` 로 전 배선이 돈다. `pytest` **89개** 통과,
> 하드웨어 선택 테스트 2개는 개발 PC에서 skip된다.
>
> 새 `transport="spi"` 경로는 8×576B WGSP 프레임을 한 번의 4608B Linux ioctl로 읽는다.
> 30초 실기에서 19,268프레임, 320.014Hz, MQTT publication 168건, 전송 오류 0을 확인했고
> Kafka offset 증가도 확인했다. 기존 UART와 replay 경로는 그대로 유지한다.
>
> **아직 없는 것**: SPI 제어 레코드가 없으므로 SPI 모드의 `train` 캘리브레이션 명령,
> 단절 구간 스풀(`spool/`), 한 시간 soak 및 네트워크 장애 복구 증적.
>
> **업링크 경계가 2026-09-10에 바뀌었다.** 이 레포는 이제 **1-D 합성 대표신호**까지만 만든다.
> S3 스칼로그램·PCA-ACF 변환은 클라우드 모델서버가 한다. 절단점은
> `features/common.py:52-93` `select_pc_signal()` 의 반환값이며, 실측으로
> **엣지 몫 p90 13.7ms / 업링크 1/117** 이다(아래 벤치).

---

## 책임 경계

| 하는 것 | 하지 않는 것 |
|---|---|
| CSI 수신 · 링버퍼 적재 | **낙상 판정** → 클라우드 |
| **재실/움직임 감지 (이 레포가 유일한 권위)** | 신호 수집 하드웨어 제어 → ESP32-C5 펌웨어 |
| 게이팅 — 활동 구간에만 업로드 | 영속 저장 → 클라우드 DB (여기 SQLite는 단절 버퍼 전용) |
| 서브캐리어 선택 · **PCA 합성 대표신호**(1-D)까지 | **S3 스칼로그램·PCA-ACF 변환** → 클라우드 모델서버 |
| MQTT/TLS 퍼블리시, 4단계 캘리브레이션 | 사용자 인증·대시보드 → 백엔드/프론트엔드 |

**네트워크가 끊기면**: 재실감지는 계속 동작한다. 낙상 감지는 **중단된다.** 안전 기능이므로 무증상으로 넘기면 안 되고, `telemetry`로 명시 보고해야 한다.

---

## 구조

```
src/wifiguard_edge/
  __main__.py      엔트리포인트 — 스레드 4개 수명 관리          [M1]
  config.py        default.toml + device.toml → dataclass       [M1]
  gating.py        업로드 게이트 (재실 기반 · ABSENT 유예)      [M1]
  feature_loop.py  FeatureLoop 0.25s — 대표신호 생성            [M1]
  presence_loop.py PresenceLoop 0.25s — 상시                    [payload 이름 정정 2026-09-10]
  presence/        재실감지 신호체인 + 상태머신                 [이식 · 무변경]
  features/        선택 → 리샘플 → PCA(엣지) → S3·ACF(클라우드) [3분해 2026-09-10 · 계약 고정]
  csi/             링버퍼 · UART 프레임 파서                     [이식 · 무변경]
  calibration/     4단계 61초 캘리브레이션            [이식 · cmd 연결은 미구현]
  transport/       base.py(Protocol) · replay_source.py          [M1]
                   wgsp_protocol.py · wgsp_source.py — Batch8 SPI [실기 검증]
  mqtt/            codec · publisher(논블로킹 큐) · command      [M1]
  spool/           단절 버퍼                                     [비어 있음]
config/            default.toml · device.toml.example
  deploy/            systemd unit · install.sh                     [통합 작업장 배포 경로 검증]
tools/             bench_pipeline.py(--stages) · replay_edge.py
tests/             89개 — protocol · presence_payload · feature_split · thread shutdown
                   config_parity · gating · transport_contract · units · no_cwt_on_edge
docs/              명세 문서
_reference/        승계하지 않는 참조 코드
```

## 설치

```bash
# 개발 PC — 네 레포가 같은 상위 폴더에 있는 통합 작업장 기준
uv sync --extra mqtt --extra dev

# 라즈베리파이
sudo bash deploy/install.sh          # SPI 활성화 · gpio 그룹 · tmpfs 로그 · systemd
# 별도 경로에 clone했다면:
# sudo CONTRACTS_DIR=/path/to/WIFIGUARD-BACKEND/packages/contracts bash deploy/install.sh
```

## 지금 돌려볼 수 있는 것

```bash
cp config/device.toml.example config/device.toml   # tenant_id · device_id 를 채운다
uv sync --extra mqtt --extra dev                   # Pi 는 cwt extra 를 넣지 않는다

python -m pytest                                   # 89개 + 하드웨어 선택 테스트 2개 skip

# ① 브로커 없이 파이프라인만 (발행 내용을 로그로)
python -m wifiguard_edge --transport replay --no-mqtt --echo --duration 20

# ② 로컬 Mosquitto 로 실제 발행
docker run -d --name wg-mosq -p 1883:1883 eclipse-mosquitto:2.0 \
  sh -c "printf 'listener 1883 0.0.0.0\nallow_anonymous true\n' > /m.conf && exec mosquitto -c /m.conf"
python -m wifiguard_edge --transport replay        # device.toml 에 broker_host=localhost, tls=false
docker exec wg-mosq mosquitto_sub -h localhost -t 'wifiguard/#' -v

# ③ 결정론적 recording 으로 재생 (회귀 비교용)
python tools/replay_edge.py synth --out recordings/synth.npz --seconds 120
python -m wifiguard_edge --transport replay --replay-source recordings/synth.npz

# ④ 실기 (Batch8 수신기 연결 시) — device.toml 의 [transport] kind="spi"
python -m wifiguard_edge
```

주요 옵션: `--no-mqtt`(브로커 없이) · `--echo`(발행 내용 로그) · `--duration N`(N초 후 종료) ·
`--transport {serial,replay,spi}` · `--log-level`.

### 벤치 — 엣지 몫과 클라우드 몫

```bash
python tools/bench_pipeline.py --fs 166.75 --stages
```

개발 PC 실측 (fs 166.75Hz, 245 서브캐리어, 40회):

| 구간 | 담당 | median | p90 | 250ms 예산 |
|---|---|---:|---:|---|
| `extract_window_signal` (선택 → 리샘플 → PCA) | **엣지** | 10.8ms | **13.7ms** | 예 (18배 여유) |
| `features_from_signal` (CWT + ACF) | 클라우드 | 49.9ms | 818.2ms | — |
| 합계 (분해 이전과 같은 일) | — | 60.6ms | 828.9ms | **아니오** |

엣지 몫은 시간의 17.8%, 업링크는 1,996B 대 233,472B = **1/117**.
합계가 예산을 넘는다는 것이 D1의 근거다 — 이 일을 Pi 에 두면 애초에 성립하지 않았다.
클라우드 쪽 p90 818ms 는 모델서버 처리량 제약으로 남아 있다(계획서 R1).

---

## 세 축의 임계값 — 절대 섞지 말 것

이름이 비슷하지만 **척도가 전혀 다르다.**

| 값 | 출처 | 척도 | UI 라벨 |
|---|---|---|---|
| `presence_mv_threshold` | 캘리브레이션 (여기) | MV 스케일, 기본 2.0 | **"움직임 임계값"** |
| `wander_baseline` | 캘리브레이션 (여기) | Welch PSD 스케일, 기본 0.5 | **"재실 baseline"** |
| `wander_ratio_threshold` | 설정 (여기) | **baseline 대비 배수** 1~5, 기본 1.8 | "WANDER 비율 임계값" |
| `threshold` = **0.468** | 모델 (**클라우드**) | **확률** 0~1 | **"판정 임계값"** |

`wander`라는 이름도 세 군데서 다른 뜻으로 쓰인다 — 여기의 `wander`(0.1–0.5Hz Welch band energy의 baseline 대비 **비율**), ESP `occupancy_fsm.c`의 `wander`(진폭 평균의 **절대 편차**, 폴백 전용·무관), 프론트 목업의 `wander_threshold`(절대값). 명세 §5.3 참조.

## 핵심 파라미터

| 재실 (MV) | 값 | | Wander | 값 |
|---|---:|---|---|---:|
| 관측 윈도우 | 3.0초 | | 관측 윈도우 | 10.0초 |
| 이동분산 창 | 0.5초 → ω=24 | | 사전필터 대역 | 0.05–5Hz |
| 리샘플 | 100Hz | | 측정 대역 | 0.1–0.5Hz |
| 밴드패스 | 0.5–50Hz, 4차 | | 디바운스 | 2.0초 |
| 선택 서브캐리어 | 10개 | | 이동분산 창 | 1.0초 → ω=50 |
| stride | 0.25초 | | 타임아웃 | 10초 |

> ω는 파생값이다: `max(1, round((mv_window_sec * fs_hz - 1) / 2))`. 파이썬 `round()`는 banker's rounding이라 **24.5 → 24, 49.5 → 50**이다. 언어를 바꾸거나 `math.floor`/`np.round`로 갈아끼우면 값이 달라진다.

**상태머신은 비대칭이다**: MV는 ABSENT→PRESENT 전이를 일으킬 수 있지만, wander는 **이미 PRESENT일 때만** 유지에 기여한다. 입실에는 반드시 몸통 동작이 따르며, 이 비대칭이 문 열림·바람 같은 순간 노이즈의 오탐을 막는다.

## 문서

- [PORTING.md](PORTING.md) — 이식 매핑 · 남은 작업 · 검증 상태
- [docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_raspberry_v1.0_20260804.md) — 이 레포의 명세
- [docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md](docs/WIFI-GUARD_레포명세_backend_v1.0_20260804.md) §7 — **4레포 공통 계약 SSOT** (MQTT 토픽·페이로드)
- [docs/CSI-Guard_재실감지_알고리즘_상태머신_보고서_v2.0_20260720.md](docs/CSI-Guard_재실감지_알고리즘_상태머신_보고서_v2.0_20260720.md) — 재실 알고리즘 원본
- [deploy/SETUP_BY_PLATFORM.md](deploy/SETUP_BY_PLATFORM.md) — 시제품 플랫폼별 설정 (install.sh 근거)
