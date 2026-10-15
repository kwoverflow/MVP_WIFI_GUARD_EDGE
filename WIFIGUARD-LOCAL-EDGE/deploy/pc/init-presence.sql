-- PC fixture only: native PostgreSQL, same columns/INSERT path, no Timescale claims.
CREATE TABLE IF NOT EXISTS presence_samples (
  ts TIMESTAMPTZ NOT NULL, facility_id TEXT NOT NULL, device_id TEXT NOT NULL,
  state TEXT NOT NULL, mv_current DOUBLE PRECISION, wander_current DOUBLE PRECISION,
  mv_threshold DOUBLE PRECISION, wander_baseline DOUBLE PRECISION,
  wander_ratio_threshold DOUBLE PRECISION, wander_ratio DOUBLE PRECISION,
  wander_confirmed BOOLEAN, last_activity_at DOUBLE PRECISION,
  seconds_since_activity DOUBLE PRECISION, just_changed BOOLEAN
);
CREATE INDEX IF NOT EXISTS presence_samples_device_ts ON presence_samples (facility_id, device_id, ts DESC);
