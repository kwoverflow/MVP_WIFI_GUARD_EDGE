# Raspberry Pi 5 완전 로컬 Edge

2026-10-08 운영 설정 확정: **Pi5 ONNX 모델 0.5초 stride·1스레드, 재실 0.5초,
미세 움직임 PSD 1초**를 사용한다. 실물 Pi5 모의 RAW 전체 소프트웨어 E2E에서 0.5초는
기한 초과/예약 누락 없이 통과했고, 0.25초의 1~3스레드 조건은 통과하지 못했다.
모델·피처·판정 수식은 유지한다. 실제 ESP32 입력·정확도·장시간 안정성 보장은 별도다.
아래 과거 측정값은 당시의 주기를 보존한다. 최신 배포 기준은 [Pi5 프로필](deploy/pi5/README.md)이다.

2026-10-10 추가: 통합 SPI 프로필에 기존 레포의 빈방 임계값 보정을 연결했다.
매 시작마다 빈방 확인 후 **퇴실 대기 30초 + 측정 30초**를 수행하고, 완료 전에는 추론을 막는다.
모델/재실 실행 주기는 위 설정 그대로다. SPI가 지원하지 않는 UART `train`/AGC 제어는 실행하지 않는다.
확인 명령·진행 조회·실패 시 동작은 [시작 보정 안내](deploy/pi5/README.md#시작할-때-빈방-보정-2026-10-10-추가)를 참고한다.

ESP32의 WGSP Batch8 CSI를 수신하고 30채널 선택 → 시간 보간 → 500샘플 리샘플 →
Q-value/PCA → S3·ACF → CPU 모델 추론 → A/B 후처리 → 로컬 알람까지 한 프로세스에서 실행한다.
Kafka, MQTT 브로커, Docker, DB, MLOps 서버 또는 외부 네트워크가 필요하지 않다.
현재 PR #2와 별도 `codex/pi5-local-edge` 브랜치에서 개발한다.

2026-10-07 추가: Pi5 통합 프로필은 사용자가 요청한 **RAW Q 상위 30개** 입력 실험과
움직임 0.5초 / 미세 움직임 PSD 1초 분리를 지원한다. 기존 고정 30개 입력은
`--model-carrier-selection training-fixed`로 보존한다. 아래의 기존 모델 동등성 결과는
이 고정 입력 경로에만 해당하며, Q 상위 30개 입력의 낙상 정확도 동등성을 뜻하지 않는다.
상세 설정·검증 범위는 [Pi5 프로필](deploy/pi5/README.md)을 따른다.

같은 날 추가한 **E코어 1개·ONNX 1스레드 전체 E2E 측정**은 Edge뿐 아니라
API·DB·UI·알림 처리까지 합산한다. 아래 과거 벤치마크의 CPU/RSS는 Edge 전용이므로
전체 사용량과 구분한다. 최신 수치와 Windows DB의 별도 VM 제한은
[Pi5 전체 자원 측정](deploy/pi5/README.md#e코어-1개-및-전체-로컬-e2e-자원-측정-2026-10-07)에,
실물에서 사용할 읽기 전용 서비스 합산 측정기는 같은 문서에 정리했다.

## 원본 모델을 유지하는 범위

- 같은 3초 입력창, 같은 30채널 선택, 같은 anti-aliasing 리샘플과 Q/PCA, 같은 GMW CWT.
- 같은 S3 224×224, ACF 1×128×64, 정규화, 체크포인트와 64개 확률 bin.
- Pi3/서버 경로의 사용하지 않는 호환용 PCA 대표신호 계산을 제거한다. 모델 PCA는 한 번 수행한다.
- S3 수직·수평 노이즈 제거를 Numba로 컴파일한다. fastmath, 근사 CWT,
  작은 입력 이미지, 모델 축소는 사용하지 않는다. ONNX FP32 실행 최적화와 INT8 PTQ
  비교 결과는 아래에 별도 기록한다. 검증에 실패한 INT8은 운영 후보에서 제외한다.
- 고정 크기 배열 링버퍼에서 수신 프레임마다 30채널을 한 번 선택한다. 기본 배열은 약 0.29 MiB다.
- ONNX/Pi5 기본 stride는 **0.5초·모델 1스레드**다. 기존 Torch 기본값은 0.25초·2스레드로
  보존하며, 명시적 `--stride-seconds 0.25`, `0.5`, `0.75`, `1`과 `--threads`는 비교용으로
  지원한다. 간격을 바꾸면 A/B 후처리의 시간 의미가 바뀌므로 원본 알람
  동작과 동등하다고 표현하지 않는다. 4회 연속 증거의 첫 결과부터 마지막 결과까지는
  stride 0.25/0.5/0.75/1초에서 각각 0.75/1.5/2.25/3초다. 이는 전체 알람지연이 아니며,
  Markov 경로가 먼저 알람을 낼 수도 있다.
- 창 중앙 확률을 쓰는 모델의 1.5초 고정 관측 지연은 그대로다. 계산시간과 알람지연을 구분한다.

누락이 20ms를 넘는 창이나 오래된 데이터는 추론하지 않는다. 장치 재부팅·오버런·공백 뒤에는
후처리를 초기화한다. 수신 스레드와 추론을 분리하고 처리 대기열을 쌓지 않으며, 처리 주기를
놓치면 최신 창으로 이동하고 `skipped_deadlines`에 기록한다. 이것은 성능 성공을 숨기는 기능이
아니다. 누락이 발생하면 실시간 통과로 판단하지 않는다.

## 설치와 첫 확인

Pi5의 64비트 Raspberry Pi OS, Python 3.11–3.13, 냉각 장치를 준비한다. 레포 전체와 외부
가중치를 함께 복사한다. 가중치는 기존 모델의
`WIFIGUARD-BACKEND/weights/temporal_segmentation_fixed_delay.pt`다.

```bash
cd /opt/wifiguard/WIFIGUARD-LOCAL-EDGE
bash deploy/install-pi5.sh
.venv/bin/wifiguard-local-benchmark \
  --checkpoint ../WIFIGUARD-BACKEND/weights/temporal_segmentation_fixed_delay.pt \
  --windows 60 --threads 2 --stride-seconds 0.25 \
  --output local-output/benchmark-025s.json
```

Pi5 전용 설치 스크립트는 PyTorch CPU wheel을 설치한다. 설치 시 인터넷은 필요하지만
실행에는 필요하지 않다. SPI 연결 전 아래 재생 모드로 실시간 실행을 검증할 수 있다.

```bash
.venv/bin/wifiguard-local-edge \
  --checkpoint ../WIFIGUARD-BACKEND/weights/temporal_segmentation_fixed_delay.pt \
  --transport replay --duration 60 --output-dir local-output/replay
```

Windows 개발 PC에서는 이 폴더에서 `uv sync --extra test` 후 `uv run`으로 동일한 명령을 실행한다.
PC 시간으로 Pi5 성능을 환산하지 않는다. `uv.lock`은 개발 재현용이며 Pi5 설치는 CPU wheel
선택이 명시된 설치 스크립트를 이용한다.

### 기존 방식과 공정하게 비교하기

피처와 모델을 따로 측정하려면 위 `wifiguard-local-benchmark`를 사용한다. CPU와 메모리를
기존 방식과 비교하려면 두 경로를 **새 프로세스에서 각각** 실행하는 아래 명령을 사용한다.
동일 가중치, 합성 320 Hz CSI, 0.25초 stride, Torch 2스레드로 각 경로를 30초씩 3회 실행한다.
실행 순서는 기존→최적화 / 최적화→기존 / 기존→최적화로 바꾼다.

```bash
uv run python -m wifiguard_local_edge.compare \
  --checkpoint ../WIFIGUARD-BACKEND/weights/temporal_segmentation_fixed_delay.pt \
  --windows 120 --repeats 3 --threads 2 --stride-seconds 0.25 \
  --output local-output/comparison/summary.json
```

서로 다른 종류의 코어를 가진 개발 PC에서는 `--cpu-affinity 0,2`처럼 두 경로를 같은 논리
CPU에 고정할 수 있다. 실제 호스트에서 사용 가능한 CPU 번호를 지정한다. 이 설정은 벤치
프로세스에만 적용하며 운영 서비스 설정을 바꾸지 않는다.

집계는 모든 창의 시간을 합쳐 median/P95/max를 다시 계산한다. CPU는 워밍업을 제외한
동일 측정시간의 프로세스 CPU 시간이고, 100%는 논리 CPU 하나의 사용량이다. 메모리는
프로세스 RSS이며 피크는 50ms 간격 표본이다. 준비/캐시 비용으로 메모리가 늘 수도 있다.
이 비교에는 SPI, MQTT/Kafka, Docker, DB, MLOps 및 외부 알림 비용이 포함되지 않으므로
**기존 분산 파이프라인 전체의 리소스 감소율로 해석하지 않는다.** 각 실행의 원시 측정은
`baseline-N.json` / `optimized-N.json`에 보존된다. 숫자 동등성은 별도 벤치로 확인한다.

### 개발 PC 실측 결과 (2026-10-01)

Intel i9-13900K / Windows 11 / RAM 약 64 GiB에서 CPU만 사용했다. 두 구현 모두 논리
CPU 0,2와 Torch 2스레드로 고정했다. 각 구현을 30초씩 3회 실행해 총 90초 / 360창을 비교했다.
코어를 고정하지 않은 예비 실행은 변경하지 않은 모델 추론 시간도 크게 달라져 최종 비교에서
제외했다. 이 표는 기존 분산 서비스 전체가 아니라 **같은 PC에서의 피처+모델 계산 비교**다.

| 지표 | 기존 | 최적화 | 변화 |
|---|---:|---:|---|
| 피처 생성 중앙값 | 21.30 ms | 6.18 ms | 시간 71.0% 감소, 3.44배 속도 |
| 모델 추론 중앙값 | 39.45 ms | 39.71 ms | 사실상 동일 |
| 피처+추론 중앙값 | 60.79 ms | 46.07 ms | 시간 24.2% 감소 |
| 피처+추론 P95 | 64.03 ms | 49.48 ms | 시간 22.7% 감소 |
| 평균 CPU (1코어=100%) | 119.55% | 112.84% | 사용량 5.6% 감소 |
| 운영 RSS | 481.06 MiB | 480.26 MiB | 사실상 동일; RAM 절감 주장 안 함 |
| 250ms 처리 기한 초과 | 0 / 360창 | 0 / 360창 | 두 구현 모두 계산 기한 통과 |

피크 RSS는 각각 약 508 / 507 MiB였다(50ms 표본). 원본과 최적화의 추가 120창 비교에서
예측 확률 최대 절대차이는 `3.64e-12`, 피처 최대 절대차이는 `4.32e-7`이었고 임계값 판정은
모두 같았다. 부동소수점 수준의 작은 차이가 있으므로 모든 입력이 비트 단위로 동일하다고
표현하지 않는다. 공급 패키지 예제 2창 동등성도 통과했고 관련 자동 테스트는 18개 통과했다.

근거 스냅샷은 [validation/desktop-cpu-comparison-20261001.json](validation/desktop-cpu-comparison-20261001.json)에 있다.
합성 CSI 계산 비교이므로 실제 낙상 정확도, ESP32/SPI E2E, 장시간 온도 안정성 또는 Pi5
실시간 성능을 검증한 결과가 아니다. 고정 1.5초 관측 지연도 이 최적화로 줄어들지 않는다.

## Pi5 1초 stride 후보: ONNX CPU 전용 실행

기본 운영 후보는 **ONNX FP32**다. 원본 모델 구조·활성 경로·가중치·정규화·피처 크기·
임계값은 유지한다. 같은 연산 그래프의 Conv/BatchNorm 결합 등 실행 최적화만 적용한다.
원래 temporal forward가 호출하지 않는 분류용 FC 등의 체크포인트 항목은 ONNX에 내보내지
않지만, 사용 중인 계층을 가지치기하거나 재학습한 것이 아니다. 원본 `.pt`는 수정하지 않았다.

INT8도 Conv/Gemm/MatMul에 대해 정적 QDQ·채널별 양자화를 수행했다. 보정과 평가 데이터는
녹화 단위로 분리했다(보정 40개 녹화/153창, 평가 37개 녹화/580창). 그러나 아래 회귀 기준에
실패해서 **운영에 사용하지 않는다**. 실패한 모델을 런타임에서 자동으로 선택하지 않는다.
양자화가 항상 더 빠르거나 같은 판정을 보장하지 않는다는 공식
[ONNX Runtime 안내](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html)와도
일치하는 결과다. 이 결과를 양자화 완료/낙상 정확도 보장으로 표현하지 않는다.

### 제한 자원 PC 비교 결과 (2026-10-01)

실제 Pi5가 없으므로 Windows i9-13900K의 **서로 다른 E-core 4개**(논리 CPU 16–19)에
프로세스를 고정하고 4스레드, GPU 미사용, 프로세스 committed-memory 2 GiB 상한을 적용했다.
상한은 Job Object 조회와 제한 초과 할당 실패 시험으로 확인했다. 이것은 RAM 2 GiB 전체
장비나 ARM/2.4 GHz Pi5를 에뮬레이션한 것이 아니다. PC의 메모리 대역폭·캐시·명령어·클럭은
그대로이며 Pi5 지연시간으로 환산할 수 없다. Pi5의 실제 구조는
[공식 사양](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-product-brief.pdf)을 따른다.

각 후보를 새 프로세스에서 60초/60창, 같은 1초 cadence로 한 번씩 측정했다. Torch 기준도
이전 단계의 최적화된 피처를 사용한다. 준비+피처는 동일한 합성 CSI, 추론은 동일한 제공
녹화 S3/ACF를 사용했다. 이 둘은 서로 다른 입력이므로 아래 표는 계산 성능 비교이며 연결된
실시간 E2E 시험이 아니다. 연결된 합성 E2E 결과는 다음 절에 별도로 기록한다.

| 지표 | 원본 Torch 실행 | ONNX FP32 (채택) | INT8 (미채택) |
|---|---:|---:|---:|
| 모델 파일 | 89.21 MiB | 87.03 MiB | 22.05 MiB |
| 준비+피처+추론 중앙값 | 114.59 ms | 63.39 ms | 76.69 ms |
| 준비+피처+추론 P95 | 129.08 ms | 69.98 ms | 82.92 ms |
| 최대 처리시간 | 133.62 ms | 77.11 ms | 90.88 ms |
| 피처 P95 | 25.15 ms | 22.45 ms | 23.17 ms |
| 추론 P95 | 104.21 ms | 48.59 ms | 61.89 ms |
| 종료 시 RSS | 466.90 MiB | 295.39 MiB | 301.14 MiB |
| 측정 중 RSS 피크 (50ms 표본) | 523.84 MiB | 298.93 MiB | 304.69 MiB |
| 시작 과정 RSS 피크 (별도) | 602.28 MiB | 381.61 MiB | 344.86 MiB |
| 평균 CPU (1코어=100%) | 21.80% | 16.74% | 16.98% |
| 1초 계산 기한 초과 | 0/60 | 0/60 | 0/60 |

FP32는 이 시험에서 P95 계산시간 **45.8%**, 종료 시 RSS **36.7%**, 평균 CPU **23.2%**
감소를 기록했다. 단일 60초 반복이며 오차 구간이나 장시간 안정성은 산출하지 않았다.
CPU 100%는 코어 하나 기준이다. 4코어 총량 기준 사용률은 표의 값의 1/4다. RSS 피크는
50ms 표본이라 순간 최대를 놓칠 수 있다. 이 메모리는 모델뿐 아니라 피처·런타임·입력도
포함하지만 OS, 다른 프로세스, SPI 커널 버퍼는 포함하지 않는다.

ONNX 전용 환경에는 PyTorch를 설치하지 않는다. ssqueezepy가 사용하지 않는 PyTorch도
자동으로 import하는 경로를 피하기 위해서다. 전체 환경과 Torch 없는 환경의 합성 입력
3개에서 S3/ACF 배열이 모두 비트 단위로 같았고, 실제 ONNX 벤치 종료 시에도 Torch가
로드되지 않은 것을 확인했다. 가상환경 파일 합계는 전체 개발 환경 약 909 MiB, ONNX
전용 약 348 MiB였다. 전체 환경에는 테스트·내보내기 도구도 포함하므로 이 차이를 모델
자체의 저장 절감률로 해석하지 않는다. ARM Linux 설치 크기는 별도 실측이 필요하다.

### 원본 출력과 후처리 회귀 검증

- 평가 580창: FP32 64-bin 확률 최대 절대오차 `1.95e-5`, 중앙 0.5 판정 변화 0개.
- 양성 라벨이 포함된 평가 녹화 4개의 전체 시계열을 1초 cadence로 재검사한 205창:
  FP32 최대 절대오차 `2.12e-5`, 중앙 0.5/증거 0.25 판정 및 A/B 출력 변화 모두 0개.
- 같은 205창에서 INT8 중앙/증거 판정이 각각 1개 바뀌었다. 알려진 양성 중앙창 11개 중
  원본이 잡은 11개를 INT8은 10개만 유지했다. 최대 64-bin 오차는 약 0.998로 기준 초과다.
  이 시계열의 최종 A/B trigger 자체는 같았지만, 원본 동등성 조건에는 실패했다.
- 580창에는 양성 6개, 음성 132개, 미상 442개가 포함된다. 205창에는 양성 11개,
  음성 182개, 미상 12개가 있다. 두 시험의 표본은 겹칠 수 있으므로 합산하지 않는다.
  기존 학습 데이터와의 중복 여부도 알 수 없다. **새로운 낙상 정확도 검증은 아니다.**

### 연결된 320Hz 모의 입력 시험

동일한 4 E-core/2 GiB 제한에서 합성 CSI를 실제 시간에 맞춰 320Hz로 **65초** 공급하고,
수신 → 30채널 선택 → 리샘플 → Q/PCA → S3/ACF → FP32 → A/B → 로컬 상태 파일을 연결했다.
20,832프레임, 63회 추론, 시작 입력창 확보 3회 외 정상 구간 invalid/stale 0회,
sequence gap 0, deadline miss/skip 0이었다. 전체 계산 P95 **96.41ms**, 최대 **100.06ms**,
종료 RSS **281.34 MiB**, 표본 피크 **284.84 MiB**였다. 합성 입력 알람은 0개이며,
이 시험은 실제 낙상 감지·ESP32/SPI 배선·RP1·외부 알림을 증명하지 않는다.

위 근거는 [validation/pi5-budget-comparison-20261001.json](validation/pi5-budget-comparison-20261001.json)에 보존한다.
결론은 **Pi5 1초 stride 실물 시험에 진입할 가치가 있다**는 것이지 Pi5 성능 보장은 아니다.
1.5초 관측 지연과 1초 후처리 cadence 때문에 알람이 1초 이내라고 보장할 수도 없다.

### PC에서 모델 생성 및 재현

이 폴더의 전체 개발 환경에서 수행한다. `--feature-root`에는 공급 패키지의 `data/features`
폴더를 지정한다. 내보낸 파일은 원본 체크포인트와 다른 폴더에 둔다.

```powershell
uv sync --extra export --extra test
.venv\Scripts\python.exe -m wifiguard_local_edge.export_model `
  --checkpoint ..\WIFIGUARD-BACKEND\weights\temporal_segmentation_fixed_delay.pt `
  --feature-root C:\path\to\InhouseSegmentationRealtime\data\features `
  --output-dir local-output\pi5-model
.venv\Scripts\python.exe -m wifiguard_local_edge.verify_ab_regression `
  --checkpoint ..\WIFIGUARD-BACKEND\weights\temporal_segmentation_fixed_delay.pt `
  --feature-root C:\path\to\InhouseSegmentationRealtime\data\features `
  --model-dir local-output\pi5-model --output local-output\pi5-model\ab-regression.json --certify
```

PC 비교에는 별도 `.venv-onnx`를 만든 후 명시된 CPU 의존성만 설치하고 세 소스 패키지를
`--no-deps -e`로 설치한다. `deploy/install-pi5-onnx.sh`와 같은 구성이다.

```powershell
.venv\Scripts\python.exe -m wifiguard_local_edge.budget_benchmark `
  --checkpoint ..\WIFIGUARD-BACKEND\weights\temporal_segmentation_fixed_delay.pt `
  --model-dir local-output\pi5-model --output-dir local-output\budget `
  --onnx-python .venv-onnx\Scripts\python.exe --windows 60
```

`budget_benchmark`의 자동 E-core 선택/메모리 제한은 Windows용이다. Linux에서 이 명령을
Pi5 에뮬레이션으로 사용하지 않는다. Pi5는 아래 실제 런타임과 systemd 자원 제한을 사용한다.
현재 생성한 배포 후보는 `local-output/pi5-model-20261001/model-fp32.onnx`와 같은 이름의
`model-fp32.json`이다. `.onnx`, 체크포인트, 공급 데이터는 Git에 추가하지 않는다.

### Pi5 설치·실행 후보

64비트 Pi OS와 **Python 3.12 또는 3.13**을 사용한다. 별도 프로필의 현재 SciPy pin 때문에
Python 3.11은 이 ONNX 설치 스크립트에서 거부한다. ARM64/Python 3.12 대상의 binary wheel
의존성 해석 dry-run은 통과했다. 실제 ARM 설치와 실행은 아직 검증하지 않았다.
레포를 `/opt/wifiguard`에 두고 위의 FP32 `.onnx`와 인증 `.json`을
`/opt/wifiguard/models/`에 복사한다. Pi에는 원본 Torch 체크포인트가 없어도 된다.

```bash
cd /opt/wifiguard/WIFIGUARD-LOCAL-EDGE
bash deploy/install-pi5-onnx.sh
.venv-onnx/bin/wifiguard-local-edge \
  --backend onnx --model /opt/wifiguard/models/model-fp32.onnx \
  --transport replay --stride-seconds 1 --threads 4 --duration 65 \
  --output-dir local-output/pi5-replay
```

이 전용 환경은 `--backend onnx`만 지원한다. 기존 패키지의 학습/PyTorch 의존성을 설치하지
않고 최소 의존성을 명시하므로 일반 개발 환경의 `uv sync`로 대체하지 않는다. export·학습·
Torch 비교는 전체 환경에서 한다. 설치 스크립트는 SPI나 서비스를 자동으로 시작하지 않는다.
systemd 예시는 `deploy/wifiguard-pi5-onnx.service`다. 현재 0.5초 stride·모델 1스레드, CPU 총량 400%,
MemoryHigh 1.5 GiB/MemoryMax 2 GiB를 사용하며 실제 사용자·SPI 권한·경로 확인 후 적용한다.
Pi5 실물에서 native ARM 추론, Batch8 SPI 수신, 온도/쓰로틀링, 충분한 기간의 deadline과
실제 입력 오류를 검증한 뒤 운영 여부를 정한다. 기존 Pi3 PR은 수정하거나 다시 푸시하지 않았다.
이번 ONNX·회귀·메모리 제한·기존 서비스 호환성·전용 피처 환경 관련 자동 시험은 26개 통과했다.

## E코어 1개 재측정 (2026-10-01)

같은 i9-13900K PC에서 **실제 E코어 1개(CPU 16)**에 프로세스 전체를 고정하고,
추론을 1스레드로 제한했다. GPU는 사용하지 않았으며 프로세스 committed-memory 상한은
2 GiB였다. 원본 체크포인트·FP32 ONNX·평가 입력의 SHA-256은 이전 시험과 같았다.
모델 재학습, 구조 변경, 임계값 변경, 양자화 재시도는 하지 않았다.

각 후보는 새 프로세스에서 같은 60개 입력을 1초 간격으로 처리했다. 준비·피처 생성은
같은 합성 CSI이고 추론은 같은 제공 녹화 S3/ACF다. 두 단계는 별개 입력이므로 이 표를
연결된 E2E 지연으로 해석하지 않는다. 이전 4 E코어 측정값은 비교 기준으로 보존한다.

| 항목 | 원본 Torch · 1 E코어 | 최적화 FP32 · 1 E코어 |
|---|---:|---:|
| 준비+피처+추론 중앙값 | 197.21 ms | 146.56 ms |
| 준비+피처+추론 P95 | 252.47 ms | 165.63 ms |
| 준비+피처+추론 최대 | 278.27 ms | 209.02 ms |
| 피처 생성 P95 | 24.79 ms | 22.80 ms |
| 모델 추론 P95 | 231.66 ms | 142.65 ms |
| 종료 RSS (모델·피처·입력·런타임 포함) | 438.55 MiB | 294.28 MiB |
| 측정 중 RSS 피크 (50ms 표본) | 444.03 MiB | 297.92 MiB |
| 시작 과정 RSS 피크 (별도) | 582.79 MiB | 372.75 MiB |
| 평균 CPU (코어 1개=100%) | 20.26% | 14.97% |
| 1초 계산 기한 초과 | 0/60 | 0/60 |

FP32는 같은 1 E코어 조건에서 Torch보다 전체 P95가 **34.4%**, 종료 RSS가 **32.9%**
작았다. FP32 P95는 이전 4 E코어의 69.98ms에서 이번 165.63ms로 약 2.37배 늘었다.
측정은 후보당 60초 한 번이므로 반복 변동의 신뢰구간은 제시하지 않는다.

별도로 같은 1 E코어에서 실제 시간에 맞춘 **320Hz 합성 CSI → 피처 → FP32 → A/B →
로컬 상태 출력**을 65초 연결 실행했다. 20,856프레임과 63회 추론을 처리했고, 전체
계산 P95 **174.75ms**, 최대 **234.08ms**, 종료 RSS **280.25 MiB**, 표본 피크
**283.10 MiB**, 시작 과정 포함 평균 CPU **20.25%**였다. deadline miss/skip,
sequence gap, 정상 구간 invalid/stale는 모두 0이다. 시작 3초의 입력창 확보 실패
3회는 정상 구간과 구분한다. 합성 입력 알람은 0개였다.

**E코어 1개가 Pi5 전체와 동급임을 검증한 것은 아니다.** PC의 x86 ISA, 캐시,
메모리 대역폭, 클럭, Windows 스케줄러는 그대로다. 2 GiB도 시스템 전체 RAM이 아니라
프로세스 commit 상한이다. 이 결과는 Pi5 실물 시험에 진입할 근거이며 실제 SPI 수신,
낙상 정확도, 발열·장시간 안정성, 1초 이내 알람을 보장하지 않는다.

전체 근거와 원시 결과 해시는
[validation/pi5-one-ecore-comparison-20261001.json](validation/pi5-one-ecore-comparison-20261001.json)에
보존했다. 원시 60개 시간으로 P95를 독립 재계산하고 각 창의 단계 합계를 확인했으며,
이번 자동 테스트는 **29개 통과, skip 0개**였다. 기존 Pi3 PR은 변경하지 않았다.

```powershell
.venv\Scripts\python.exe -m wifiguard_local_edge.budget_benchmark `
  --checkpoint ..\WIFIGUARD-BACKEND\weights\temporal_segmentation_fixed_delay.pt `
  --model-dir local-output\pi5-model-20261001 `
  --output-dir local-output\pi5-one-ecore-20261001 `
  --onnx-python .venv-onnx\Scripts\python.exe `
  --windows 60 --cpu-affinity 16 --threads 1 --variants torch fp32
.venv-onnx\Scripts\python.exe -m wifiguard_local_edge.runtime `
  --backend onnx --model local-output\pi5-model-20261001\model-fp32.onnx `
  --transport replay --stride-seconds 1 --threads 1 --cpu-affinity 16 `
  --memory-limit-mib 2048 --duration 65 `
  --output-dir local-output\pi5-live-one-ecore-20261001
```

CPU 16번은 이 PC에서 확인한 E코어 번호다. 다른 PC에서는 `--cpu-affinity`를 생략하면
요청한 `--threads` 개수만큼 E코어를 자동 선택한다. 위 경로를 재사용하면 그 폴더의
결과가 갱신되므로 반복 시험은 새로운 `--output-dir`에 저장한다.

## 0.5초·0.75초 stride 연속 실행 비교 (2026-10-01)

원본 구조와 가중치의 FP32 ONNX를 그대로 사용하고, 같은 PC E코어 1개(CPU 16),
추론 1스레드, GPU 미사용, 프로세스 commit 2 GiB 상한에서 각 간격을 새 프로세스로
65초씩 시험했다. 입력은 320Hz 합성 CSI를 실제 시간에 맞춰 Batch8로 공급했다.
CSI → 준비 → 피처 → 모델 → A/B → 로컬 상태 출력이 연결된 실행이며, ESP32/SPI 실물,
실제 낙상 입력, 외부 알림 전달은 포함하지 않는다. 1초 기준은 앞선 같은 조건의 시험이다.

| 항목 | stride 0.5초 | stride 0.75초 | stride 1초 · 이전 기준 |
|---|---:|---:|---:|
| 실행 시간 | 65.156초 | 65.422초 | 65.171초 |
| 추론 횟수 | 125 | 84 | 63 |
| 준비+피처+추론+A/B 계산 P95 | 234.72ms | 163.87ms | 174.75ms |
| 계산 최대 | 328.97ms | 196.82ms | 234.08ms |
| 최대 관측값 기준 다음 주기까지 여유 | 171.03ms | 553.18ms | 765.92ms |
| 평균 CPU · 코어 1개=100% | 34.18% | 24.10% | 20.25% |
| 종료 RSS · 모델·피처·버퍼·런타임 포함 | 280.05 MiB | 280.19 MiB | 280.25 MiB |
| RSS 피크 · 50ms 표본 | 283.63 MiB | 283.72 MiB | 283.10 MiB |
| deadline miss / skipped deadline | 0 / 0 | 0 / 0 | 0 / 0 |
| 정상 구간 invalid / stale / sequence gap | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 입력창 확보 전 invalid · 시작 3초 | 6 | 4 | 3 |

CPU 평균의 측정 구간에는 시작 과정도 포함한다(각각 66.550/66.770/66.591초).
RSS는 시스템 전체 메모리가 아니라 프로세스 사용량이고, 50ms 표본 피크는 순간 피크를
놓칠 수 있다. 각 조건은 한 번만 실행했으므로 간격 변경만으로 P95 차이가 발생했다고
단정하거나, 반복 변동·장시간 안정성을 보장하지 않는다. 종료 source.frames_ok와
buffer.total_frames는 서로 다른 시점의 스냅샷이다. 0.5초 결과의 20,856 대 20,848 차이는
stop/join 사이 Batch8 한 묶음의 시점 차이이며, sequence gap 계수는 0이다.

두 새 간격 모두 로컬 타이밍 gate를 통과했다. **0.5초는 응답 빈도 우선의 Pi5 실물 시험
후보**, 0.75초는 계산 여유를 더 두는 후보로 볼 수 있다. 실제 Pi5 성능이 충분하다고
확정한 것은 아니다. 이전 최대 234ms를 모든 간격의 상한으로 사용할 수 없으며, 이번
0.5초 실행 최대는 329ms였다. 0.25초 연속 실행 통과를 의미하지 않는다.

모델의 3초 창과 1.5초 고정 관측 지연은 그대로다. A/B의 Markov·EMA·4회 연속 증거는
업데이트 횟수 기반이므로 stride 변경에 따라 알람 확정 시점이 달라질 수 있다.
후처리 파라미터나 서비스 기본 stride는 바꾸지 않았다. 자동 테스트는 **37개 통과,
skip 0개**였고 기존 Pi3 PR은 변경하지 않았다.

원시 결과 해시와 정의는
[validation/pi5-stride-comparison-20261001.json](validation/pi5-stride-comparison-20261001.json)에
보존한다. 아래 명령에서 0.5를 0.75로 바꾸고 출력 폴더를 따로 지정해 재현할 수 있다.

```powershell
.venv-onnx\Scripts\python.exe -m wifiguard_local_edge.runtime `
  --backend onnx --model local-output\pi5-model-20261001\model-fp32.onnx `
  --transport replay --stride-seconds 0.5 --threads 1 --cpu-affinity 16 `
  --memory-limit-mib 2048 --duration 65 `
  --output-dir local-output\pi5-live-one-ecore-05s-20261001
```

## SPI 실행

Pi5의 40핀 헤더 배선은 GPIO 번호와 물리 번호를 구분한다. 현재 ESP32 수신기의 설정된
SPI/READY 핀을 Pi5의 SPI0에 연결한다. Pi 쪽 물리 번호는 아래와 같다.

| 신호 | BCM GPIO | 물리 핀 |
|---|---:|---:|
| CS0 | 8 | 24 |
| SCLK | 11 | 23 |
| MOSI | 10 | 19 |
| MISO | 9 | 21 |
| READY | 25 | 22 |
| 공통 GND | — | 6 |

SPI를 활성화하고 `/sys/module/spidev/parameters/bufsiz`가 8192인지 확인한다.
GPIO chip 번호는 Pi5 OS에 따라 바뀔 수 있으므로 프로그램은 `GPIO25` 선을 가진 chip을 찾는다.
필요하면 `--gpio-chip /dev/gpiochipN`으로 지정한다. GPIO와 SPI 접근권한이 있는 사용자로 실행한다.
Pi3 프로세스가 같은 ESP32/SPI를 동시에 사용하면 안 된다.

```bash
.venv/bin/wifiguard-local-edge \
  --checkpoint ../WIFIGUARD-BACKEND/weights/temporal_segmentation_fixed_delay.pt \
  --transport spi --duration 120 --output-dir local-output/pi5-spi
```

SPI adapter는 검증된 Batch8 direct ioctl을 재사용한다. mode=0, bits=8을 읽어 확인하고
READY가 올라온 뒤 descriptor를 연다. 실행 중 mode/speed 설정 ioctl로 CS를 건드리지 않는다.
Pi5는 RP1 SPI 컨트롤러를 사용하므로 기존 Pi3 실물 성공을 Pi5 실물 성공으로 간주하지 않는다.

## 로컬 결과와 성능 판정

- `status.json`: 매 주기 상태, 단계별 median/P95/max ms, CPU·RSS 메모리·수신 품질.
- `summary.json`: 정상 종료한 실행의 최종 요약.
- `events.jsonl`: B trigger가 0→1로 바뀌는 로컬 낙상 알람. 4 MiB씩 총 4개 파일로 순환한다.
- 원시 CSI나 피처 텐서는 매 주기 저장하지 않는다. 알람 출력은 이 파일과 상태 값이다.
  실제 이메일·휴대폰 외부 발송은 이 로컬 프로세스에 연결하지 않는다.

실시간 후보 기준은 최소 30창에서 **피처+추론 P95 ≤ stride의 80%**, 최대시간 ≤ stride,
원본 피처·확률 오차 ≤ 0.0003, 동일 임계값 판정이다. 벤치 결과의 `pi5_compute_gate_passed`는
실제 Pi5 호스트에서만 true가 된다. 이 벤치는 SPI/재실·낙상 정확도/장시간 온도 안정성까지
증명하지 않는다.

SPI 실행에서 `inferences`가 계속 증가하고 `deadline_misses=0`, `skipped_deadlines=0`인지
확인한다. 시작 3초의 `invalid_windows`/`stale_windows`는 입력창 확보 구간이므로 별도 구분한다.
그 이후 수신 오류·공백·프로토콜 reject도 확인해야 한다. 0.25초 실패 시 1초 stride와
`--threads 1|2|3|4`를 측정해 비교하고, 1초도 실패하면 상위 장비로 이전한다.

원본 공급 패키지와 독립 수치 비교를 추가하려면 벤치 명령에
`--supplier-root /path/to/InhouseSegmentationRealtime`을 준다. 합성 CSI 검증은 낙상 정확도 검증이 아니다.

systemd 자동 실행 예시는 `deploy/wifiguard-local-edge.service`에 있다. `/opt/wifiguard` 설치 위치와
`wifiguard` 사용자에 맞춘 예시이며 적용 전 실제 경로·그룹·가중치 읽기권한을 확인한다.
stride/스레드는 `/etc/wifiguard-local-edge.env`의 `STRIDE_SECONDS`와 `CPU_THREADS`로 선택한다.

공식 하드웨어 근거: [Pi5 RP1 I/O](https://www.raspberrypi.com/documentation/computers/io-controllers.html),
[GPIO chip 번호 변경 안내](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-006553-WP/A-history-of-GPIO-usage-on-Raspberry-Pi-devices-and-current-best-practices),
[PyTorch CPU 설치](https://pytorch.org/get-started/locally/).

## PR 백엔드·DB·화면까지 Pi5에 포함하는 프로필

모델 단독 실행과 별개로, 기존 PR의 재실 계산·API·DB·WebSocket·알림 분기를
같은 Pi5에서 실행하도록 하는 선택형 프로필을 추가했다.
Kafka/Docker/MLOps 없이 실행하는 구성, PC 통합 검증 결과 및 미검증 범위는
[Pi5 통합 프로필 안내](deploy/pi5/README.md)를 참조한다. 원래 Pi3 PR 배포 설정은 그대로다.
