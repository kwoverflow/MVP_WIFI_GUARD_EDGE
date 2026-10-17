-- wifiguard-db EC2 · timescale/timescaledb:2.17.2-pg16 컨테이너(wg-ts, 호스트 5433, DB wifiguard_ts) 에 1회 적용.
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 재실 지표 hypertable — MQTT PresenceMsg(명세 backend §5.1) 11개 스칼라 그대로. facility/device는 토픽에서 온다.
-- 필드명은 MQTT 계약을 따른다 (WS /ws/live 의 presence_* 접두 이름과 다르다 — REVIEW_20260907.md §6.2).
CREATE TABLE IF NOT EXISTS presence_samples (
  ts                      TIMESTAMPTZ      NOT NULL,
  facility_id             TEXT             NOT NULL,
  device_id               TEXT             NOT NULL,
  state                   TEXT             NOT NULL,
  mv_current              DOUBLE PRECISION,
  wander_current          DOUBLE PRECISION,
  mv_threshold            DOUBLE PRECISION,
  wander_baseline         DOUBLE PRECISION,
  wander_ratio_threshold  DOUBLE PRECISION,
  wander_ratio            DOUBLE PRECISION,
  wander_confirmed        BOOLEAN,
  last_activity_at        DOUBLE PRECISION,
  seconds_since_activity  DOUBLE PRECISION,
  just_changed            BOOLEAN
);
SELECT create_hypertable('presence_samples', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS presence_samples_device_ts ON presence_samples (facility_id, device_id, ts DESC);
SELECT add_retention_policy('presence_samples', INTERVAL '30 days', if_not_exists => TRUE);
