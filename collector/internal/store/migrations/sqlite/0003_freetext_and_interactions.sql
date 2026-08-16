-- 0003_freetext_and_interactions.sql — completes the M1/M2 deferrals now that the
-- collector ingests arbitrary/custom-resource utilization and an 'interactions'
-- data source (SQLite dialect; mirrors ../postgres/0003_*.sql).
--
-- SQLite cannot ALTER a column CHECK in place, so each table is rebuilt (a no-op
-- copy on a fresh DB). Both rebuilds preserve every column, index, and unique key.

-- 1. metric_samples.resource -> free-text (drop the 5-value CHECK).
CREATE TABLE metric_samples_new (
    id            INTEGER PRIMARY KEY,
    cluster_id    INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    workload_uid  TEXT NOT NULL,
    resource      TEXT NOT NULL,
    resource_kind TEXT NOT NULL DEFAULT 'standard' CHECK (resource_kind IN ('standard', 'network', 'custom')),
    ts            TEXT NOT NULL,
    value         REAL NOT NULL,
    unit          TEXT,
    is_rate       INTEGER NOT NULL DEFAULT 0,
    collected_at  TEXT NOT NULL,
    UNIQUE (cluster_id, workload_uid, resource, ts)
);
INSERT INTO metric_samples_new (id, cluster_id, workload_uid, resource, resource_kind, ts, value, unit, is_rate, collected_at)
    SELECT id, cluster_id, workload_uid, resource, resource_kind, ts, value, unit, is_rate, collected_at FROM metric_samples;
DROP TABLE metric_samples;
ALTER TABLE metric_samples_new RENAME TO metric_samples;
CREATE INDEX idx_metric_samples_lookup    ON metric_samples (cluster_id, workload_uid, resource, ts);
CREATE INDEX idx_metric_samples_collected ON metric_samples (collected_at);

-- 2. data_sources.type -> add 'interactions'.
CREATE TABLE data_sources_new (
    id              INTEGER PRIMARY KEY,
    cluster_id      INTEGER REFERENCES clusters(id) ON DELETE CASCADE,
    type            TEXT NOT NULL CHECK (type IN ('prometheus', 'custom_api', 'file', 'opencost', 'mesh', 'interactions')),
    name            TEXT NOT NULL,
    endpoint        TEXT,
    auth_config     TEXT,
    settings        TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    health          TEXT,
    last_checked_at TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO data_sources_new (id, cluster_id, type, name, endpoint, auth_config, settings, enabled, health, last_checked_at, created_at)
    SELECT id, cluster_id, type, name, endpoint, auth_config, settings, enabled, health, last_checked_at, created_at FROM data_sources;
DROP TABLE data_sources;
ALTER TABLE data_sources_new RENAME TO data_sources;
