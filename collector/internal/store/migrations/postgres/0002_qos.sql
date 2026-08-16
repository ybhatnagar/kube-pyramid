-- 0002_qos.sql — QoS recommender additions (PostgreSQL dialect). PURELY ADDITIVE
-- over the verbatim-vendored 0001_init.sql: new columns + new tables only, so no
-- existing (vendored) table's data or constraints are disturbed. Mirrors
-- ../sqlite/0002_qos.sql. Contract: docs/04 §B.
--
-- DEFERRED to the collector milestone (kept out of M1 so both dialects stay in
-- lockstep with the SQLite side, where a column CHECK cannot be altered in place):
--   * relax metric_samples.resource from the 5-value CHECK to free-text (docs/04);
--   * extend data_sources.type with 'interactions' (docs/04).
-- On Postgres these are one-line ALTER ... DROP/ADD CONSTRAINT; they land alongside
-- the SQLite table-rebuild in the same future migration.

-- ---------------------------------------------------------------------------
-- 1. run_type discriminator on analysis_runs.
-- ---------------------------------------------------------------------------
ALTER TABLE analysis_runs
    ADD COLUMN run_type TEXT NOT NULL DEFAULT 'qos'
    CHECK (run_type IN ('job', 'maintenance', 'qos'));

CREATE INDEX idx_analysis_runs_type ON analysis_runs (run_type);

-- ---------------------------------------------------------------------------
-- 2. disc_workloads current-state columns.
-- ---------------------------------------------------------------------------
ALTER TABLE disc_workloads ADD COLUMN current_qos TEXT;
ALTER TABLE disc_workloads ADD COLUMN current_priority INTEGER;
ALTER TABLE disc_workloads ADD COLUMN priority_class_name TEXT;

-- ---------------------------------------------------------------------------
-- 3. metric_samples.resource_kind (additive; free-text `resource` relaxation
--    deferred — see header).
-- ---------------------------------------------------------------------------
ALTER TABLE metric_samples
    ADD COLUMN resource_kind TEXT NOT NULL DEFAULT 'standard'
    CHECK (resource_kind IN ('standard', 'network', 'custom'));

-- ---------------------------------------------------------------------------
-- 4. allocations — the N-dimensional allocation vector.
-- ---------------------------------------------------------------------------
CREATE TABLE allocations (
    id            BIGSERIAL PRIMARY KEY,
    cluster_id    BIGINT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    workload_uid  TEXT NOT NULL,
    resource      TEXT NOT NULL,                    -- free-text; standard names canonical
    resource_kind TEXT NOT NULL DEFAULT 'standard' CHECK (resource_kind IN ('standard', 'network', 'custom')),
    requested     DOUBLE PRECISION,                 -- null if unset
    "limit"       DOUBLE PRECISION,                 -- null if unset
    unit          TEXT,
    is_custom     BOOLEAN NOT NULL DEFAULT FALSE,
    source        TEXT,                             -- 'ksm' | 'k8s_api'
    collected_at  TIMESTAMPTZ NOT NULL,
    UNIQUE (cluster_id, workload_uid, resource)
);
CREATE INDEX idx_allocations_lookup ON allocations (cluster_id, workload_uid);

-- ---------------------------------------------------------------------------
-- 5. Tier-4 QoS result tables.
-- ---------------------------------------------------------------------------
CREATE TABLE qos_groups (
    id           BIGSERIAL PRIMARY KEY,
    run_id       BIGINT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    group_index  INTEGER NOT NULL,
    label        TEXT,
    centroid     JSONB,
    member_count INTEGER
);
CREATE INDEX idx_qos_groups_run ON qos_groups (run_id);

CREATE TABLE qos_recommendations (
    id                   BIGSERIAL PRIMARY KEY,
    run_id               BIGINT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    group_id             BIGINT REFERENCES qos_groups(id) ON DELETE CASCADE,
    workload_uid         TEXT NOT NULL,
    workload_kind        TEXT,
    workload_name        TEXT,
    namespace            TEXT,
    current_qos          TEXT,
    current_priority     INTEGER,
    recommended_qos      TEXT CHECK (recommended_qos IN ('Guaranteed', 'Burstable', 'BestEffort')),
    recommended_priority INTEGER,
    weighted_score       DOUBLE PRECISION,
    comparison_scope     TEXT CHECK (comparison_scope IN ('within_group', 'cross_group')),
    estimated_savings    DOUBLE PRECISION,
    savings_currency     TEXT,
    confidence           TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    summary_text         TEXT
);
CREATE INDEX idx_qos_rec_run ON qos_recommendations (run_id);

CREATE TABLE qos_evidence (
    id                   BIGSERIAL PRIMARY KEY,
    recommendation_id    BIGINT NOT NULL REFERENCES qos_recommendations(id) ON DELETE CASCADE,
    resource             TEXT NOT NULL,
    representative_value DOUBLE PRECISION,
    percentile           DOUBLE PRECISION,
    weight               DOUBLE PRECISION,
    series               JSONB
);
CREATE INDEX idx_qos_evidence_rec ON qos_evidence (recommendation_id);

CREATE TABLE qos_peers (
    id                BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL REFERENCES qos_recommendations(id) ON DELETE CASCADE,
    peer_workload_uid TEXT,
    peer_workload     TEXT,
    relation          TEXT,
    affinity          DOUBLE PRECISION
);
CREATE INDEX idx_qos_peers_rec ON qos_peers (recommendation_id);
