"""계약 파리티 — 이름 드리프트가 다시 생기면 여기서 잡는다.

이 테스트가 존재하는 이유: 재실 필드는 원래 엣지 dataclass(`PresenceStatus`)와 DB 컬럼
(`presence_samples`)이 문자 단위로 일치했는데, 중간의 `presence_loop._payload()` 하나가
3개를 `presence_*` 로 개명하고 `seconds_since_activity` 를 버리고 있었다. 그 결과 DB 컬럼
하나가 영원히 NULL 이 될 운명이었고, 아무 테스트도 이를 잡지 못했다.

세 지점이 같은 이름을 쓴다는 것은 **런타임에 드러나지 않는 종류의 계약**이라, 이렇게
명시적으로 단언하지 않으면 다음 사람이 조용히 깨뜨린다.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from wifiguard_contracts import kafka, mqtt, realtime, topics

# ── 1. presence 이름 3자 대조 ────────────────────────────────────────
#: 엣지 `PresenceStatus`(state_machine.py:25-35). 엣지 레포는 여기서 import 할 수 없으므로
#: (별개 레포·별개 venv) 값을 복제하고, 엣지 쪽 `test_config_parity` 가 반대편을 지킨다.
PRESENCE_STATUS_FIELDS = (
    "state",
    "mv_current",
    "wander_current",
    "mv_threshold",
    "wander_baseline",
    "wander_ratio_threshold",
    "wander_ratio",
    "wander_confirmed",
    "last_activity_at",
    "seconds_since_activity",
    "just_changed",
)


def _presence_samples_columns() -> set[str]:
    """`presence_samples` 의 데이터 컬럼 (메타 3개 제외).

    `wifiguard_db` 가 설치돼 있으면 실제 Table 에서 읽고, 아니면 DDL 원문에서 읽는다.
    contracts 패키지가 db 패키지에 의존하지 않게 하려는 것이다 — 의존 방향은 db → contracts 다.
    """
    meta = {"ts", "facility_id", "device_id"}
    try:
        from wifiguard_db.models.presence_sample import presence_samples

        return {c.name for c in presence_samples.columns} - meta
    except ImportError:  # pragma: no cover - db 미설치 환경
        sql = Path(__file__).resolve().parents[3] / "deploy" / "aws" / "init-timescale.sql"
        if not sql.exists():
            pytest.skip("wifiguard_db 도 init-timescale.sql 도 없다")
        body = sql.read_text(encoding="utf-8").split("CREATE TABLE", 1)[1].split("(", 1)[1].split(");", 1)[0]
        names = {line.strip().split()[0] for line in body.splitlines() if line.strip()}
        return {n for n in names if n and not n.startswith(")")} - meta


def test_presence_msg_matches_presence_status_fields():
    """MQTT PresenceMsg ⊇ PresenceStatus 11필드 (봉투 필드는 추가로 허용)."""
    assert set(PRESENCE_STATUS_FIELDS) <= set(mqtt.PresenceMsg.model_fields)


def test_presence_msg_matches_db_columns():
    """MQTT PresenceMsg 의 상태 필드 == presence_samples 데이터 컬럼. 양방향 일치를 요구한다.

    한쪽만 늘어나도 실패한다 — 컬럼을 추가하면 계약도, 계약을 늘리면 마이그레이션도 해야 한다.
    """
    assert set(PRESENCE_STATUS_FIELDS) == _presence_samples_columns()


def test_presence_status_fields_constant_is_accurate():
    """mqtt.PRESENCE_STATUS_FIELDS 가 실제 모델과 어긋나지 않게."""
    assert set(mqtt.PRESENCE_STATUS_FIELDS) == set(PRESENCE_STATUS_FIELDS)


def test_ws_presence_block_uses_same_names():
    """WS 까지 이름이 살아남는지 — 전 구간에서 리네임이 0회여야 한다."""
    assert set(PRESENCE_STATUS_FIELDS) <= set(realtime.PresenceBlock.model_fields)


def test_no_presence_prefix_anywhere():
    """`presence_*` 접두가 되살아나지 않게 (state/mv/wander 축에 한해)."""
    for model in (mqtt.PresenceMsg, realtime.PresenceBlock):
        assert not [f for f in model.model_fields if f.startswith("presence_")], model


# ── 2. 신호 인코딩 왕복 ──────────────────────────────────────────────
def test_signal_roundtrip_is_bit_exact():
    """float32 raw base64 는 무손실이어야 한다 — 양자화를 도입하면 이 테스트가 깨진다."""
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(0)
    original = rng.standard_normal(500).astype(np.float32)

    encoded = mqtt.encode_signal(original)
    decoded = mqtt.decode_signal(encoded, len(original))

    assert np.array_equal(decoded, original), "비트 동등해야 한다"
    assert decoded.dtype == np.float32


def test_signal_length_mismatch_rejected():
    np = pytest.importorskip("numpy")
    encoded = mqtt.encode_signal(np.zeros(10, dtype=np.float32))
    with pytest.raises(ValueError, match="길이 불일치"):
        mqtt.decode_signal(encoded, 11)


def test_signal_payload_size_is_as_designed():
    """fs=166.75 → T=500 이면 b64 2,668B. 4Hz 시 96kbps/기기라는 계산의 근거."""
    np = pytest.importorskip("numpy")
    encoded = mqtt.encode_signal(np.zeros(500, dtype=np.float32))
    assert len(encoded) == 2668
    assert len(base64.b64decode(encoded)) == 2000


def test_amplitude_roundtrip_is_bit_exact():
    np = pytest.importorskip("numpy")
    original = np.random.default_rng(1).standard_normal((960, 30)).astype(np.float32)
    encoded = mqtt.encode_amplitude(original)
    decoded = mqtt.decode_amplitude(encoded, 960, 30)
    assert np.array_equal(decoded, original)
    assert decoded.dtype == np.float32
    assert len(base64.b64decode(encoded)) == 115_200


def test_amplitude_length_mismatch_is_rejected():
    np = pytest.importorskip("numpy")
    encoded = mqtt.encode_amplitude(np.zeros((10, 30), dtype=np.float32))
    with pytest.raises(ValueError, match="진폭 길이 불일치"):
        mqtt.decode_amplitude(encoded, 11, 30)


def test_amplitude_contract_fields_are_all_or_none():
    with pytest.raises(ValueError, match="모두 함께"):
        _signal_msg(amplitude_b64="AAAAAA==")


# ── 3. 토픽 ─────────────────────────────────────────────────────────
def test_topic_roundtrip():
    tenant, device = f"home-{uuid4()}", uuid4()
    topic = topics.leaf_topic(tenant, device, "presence")
    parsed = topics.parse(topic)
    assert parsed.tenant == tenant
    assert parsed.device_id == str(device)
    assert parsed.leaf == "presence"


def test_device_base_matches_legacy_issue_mqtt_topic():
    """구 `routers/devices.py:issue_mqtt_topic()` 이 만들던 문자열과 동일해야 한다.

    이미 발급된 기기의 `devices.mqtt_topic` 값이 바뀌면 안 된다.
    """
    facility, device = uuid4(), uuid4()
    assert topics.device_base(str(facility), device) == f"wifiguard/{facility}/{device}"

    user = uuid4()
    tenant = topics.tenant_id(facility_id=None, user_id=user)
    assert topics.device_base(tenant, device) == f"wifiguard/home-{user}/{device}"


@pytest.mark.parametrize("bad", ["a/b", "a+b", "a#b", ""])
def test_topic_rejects_mqtt_wildcards(bad):
    with pytest.raises(ValueError):
        topics.device_base(bad, uuid4())


def test_topic_rejects_unknown_leaf():
    with pytest.raises(ValueError, match="알 수 없는 leaf"):
        topics.parse(f"wifiguard/{uuid4()}/{uuid4()}/window")


def test_window_leaf_is_gone():
    """233KB 텐서 업링크(WindowMsg)는 D1 으로 폐기됐다. 계약에 흔적이 남으면 안 된다."""
    assert "window" not in topics.UPLINK_LEAVES + topics.DOWNLINK_LEAVES
    assert not hasattr(mqtt, "WindowMsg")


def test_kafka_topic_names_match_provisioning_script():
    """`deploy/aws/user-data-kafka.sh` 가 실제로 만드는 토픽과 일치해야 한다."""
    script = Path(__file__).resolve().parents[3] / "deploy" / "aws" / "user-data-kafka.sh"
    if not script.exists():  # pragma: no cover
        pytest.skip("user-data-kafka.sh 없음")
    body = script.read_text(encoding="utf-8")
    for topic in topics.KAFKA_TOPICS:
        assert topic in body, f"{topic} 이 프로비저닝 스크립트에 없다"


# ── 4. 봉투·판별자 ──────────────────────────────────────────────────
def _signal_msg(**over):
    base = dict(
        device_id=uuid4(),
        tenant_id=f"home-{uuid4()}",
        ts=datetime.now(UTC),
        seq=1,
        signal_b64="AAAAAA==",
        signal_len=1,
        fs_hz=166.75,
        window_samples=500,
        window_span_s=3.0,
        selected_subcarrier_count=30,
        selected_stream_count=12,
        selected_pc_indices="0;2",
        candidate_pc_count=3,
        selected_pc_count=2,
        input_frames=520,
        input_subcarriers=245,
        presence_state="present",
        gate_reason="present",
    )
    return mqtt.SignalMsg(**{**base, **over})


def test_feature_record_discriminates_payload():
    """`csi-feature-stream` 에 presence 와 signal 이 섞여도 판별자로 갈라져야 한다."""
    rec = kafka.FeatureRecord(
        received_at=datetime.now(UTC),
        source_topic="wifiguard/t/d/signal",
        tenant_id="t",
        device_id=uuid4(),
        payload=_signal_msg(),
    )
    dumped = rec.model_dump_json()
    assert kafka.FeatureRecord.model_validate_json(dumped).payload.kind == "signal"


def test_unknown_field_is_rejected():
    """extra='forbid' — 엣지의 오타 필드가 조용히 무시되면 안 된다."""
    with pytest.raises(ValueError):
        _signal_msg(mv_currnet=1.0)


def test_inference_result_requires_postprocess():
    """어느 후처리로 판정했는지 모르면 사후에 오탐률을 해석할 수 없다."""
    with pytest.raises(ValueError):
        kafka.InferenceResult(
            tenant_id="t",
            device_id=uuid4(),
            ts=datetime.now(UTC),
            seq=1,
            proba_fall=0.9,
            threshold=0.468,
            inferred_at=datetime.now(UTC),
        )


def test_fall_and_presence_are_optional_in_live():
    """안전 요구 — 낙상/재실 블록의 부재가 표현 가능해야 한다 ('감지 미동작')."""
    live = realtime.DeviceLive(device_id=uuid4())
    assert live.fall is None and live.presence is None and live.online is None
