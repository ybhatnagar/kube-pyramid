-- 0002_qos.sql — QoS recommender additions (SQLite dialect). PURELY ADDITIVE over
-- the verbatim-vendored 0001_init.sql: new columns + new tables only, so no
-- existing (vendored) table's data or constraints are disturbed. Mirrors
-- ../postgres/0002_qos.sql. Contract: docs/04 §B.
--
-- DEFERRED (non-additive edits to vendored tables — SQLite cannot ALTER a CHECK;
-- not needed for the M1 synthetic path, which ranks cpu/memory utilization and
-- clusters on the new `allocations` table). To be applied in the collector
-- milestone when custom/free-text utilization + the 'interactions' data-source
-- type are actually collected:
--   * relax metric_samples.resource from the 5-value CHECK to free-text (docs/04);
--   * extend data_sources.type with 'interactions' (docs/04).

-- ---------------------------------------------------------------------------
-- 1. run_type discriminator on analysis_runs.
--    Default 'qos' (the only value produced in this standalone repo); the CHECK
--    keeps 'job'/'maintenance' so the vendored polymorphic plumbing stays intact.
-- ---------------------------------------------------------------------------
ALTER TABLE analysis_runs
    ADD COLUMN run_type TEXT NOT NULL DEFAULT 'qos'
    CHECK (run_type IN ('job', 'maintenance', 'qos'));

CREATE INDEX idx_analysis_runs_type ON analysis_runs (run_type);

-- ---------------------------------------------------------------------------
-- 2. disc_workloads current-state columns (current -> recommended in the UI).
-- ---------------------------------------------------------------------------
ALTER TABLE disc_workloads ADD COLUMN current_qos TEXT;
ALTER TABLE disc_workloads ADD COLUMN current_priority INTEGER;
ALTER TABLE disc_workloads ADD COLUMN priority_class_name TEXT;

-- ---------------------------------------------------------------------------
-- 3. metric_samples.resource_kind (additive; standard|network|custom).
--    The free-text relaxation of `resource` itself is deferred (see header).
-- ---------------------------------------------------------------------------
ALTER TABLE metric_samples
    ADD COLUMN resource_kind TEXT NOT NULL DEFAULT 'standard'
    CHECK (resource_kind IN ('standard', 'network', 'custom'));

-- ---------------------------------------------------------------------------
-- 4. allocations — the N-dimensional allocation vector (one row per
--    workload x resource); the QoS clusterer's feature source. `resource` is
--    free-text here (new table, no vendored CHECK to relax), so custom/extended
--    resources (e.g. nvidia.com/gpu) are supported from day one.
--    Effective allocation (engine) = requested ?? limit ?? max-util(last 3) ?? 0.
-- ---------------------------------------------------------------------------
CREATE TABLE allocations (
    id            INTEGER PRIMARY KEY,
    cluster_id    INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    workload_uid  TEXT NOT NULL,
    resource      TEXT NOT NULL,                    -- free-text; standard names canonical
    resource_kind TEXT NOT NULL DEFAULT 'standard' CHECK (resource_kind IN ('standard', 'network', 'custom')),
    requested     REAL,                             -- null if unset
    "limit"       REAL,                             -- null if unset
    unit          TEXT,
    is_custom     INTEGER NOT NULL DEFAULT 0,
    source        TEXT,                             -- 'ksm' | 'k8s_api'
    collected_at  TEXT NOT NULL,
    UNIQUE (cluster_id, workload_uid, resource)
);
CREATE INDEX idx_allocations_lookup ON allocations (cluster_id, workload_uid);

-- ---------------------------------------------------------------------------
-- 5. Tier-4 QoS result tables (separate from the vendored job `recommendations`
--    tables, which stay untouched).
-- ---------------------------------------------------------------------------
CREATE TABLE qos_groups (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    group_index  INTEGER NOT NULL,                  -- 0..k-1; single synthetic group in cross-cluster mode
    label        TEXT,                              -- human summary, e.g. "large stateful (high cpu+mem)"
    centroid     TEXT,                              -- JSON: per-resource centroid (scaled + original)
    member_count INTEGER
);
CREATE INDEX idx_qos_groups_run ON qos_groups (run_id);

CREATE TABLE qos_recommendations (
    id                   INTEGER PRIMARY KEY,
    run_id               INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    group_id             INTEGER REFERENCES qos_groups(id) ON DELETE CASCADE,
    workload_uid         TEXT NOT NULL,
    workload_kind        TEXT,
    workload_name        TEXT,
    namespace            TEXT,
    current_qos          TEXT,                       -- Guaranteed|Burstable|BestEffort|null
    current_priority     INTEGER,
    recommended_qos      TEXT CHECK (recommended_qos IN ('Guaranteed', 'Burstable', 'BestEffort')),
    recommended_priority INTEGER,                    -- score-proportional, clamped
    weighted_score       REAL,                       -- 0..1
    comparison_scope     TEXT CHECK (comparison_scope IN ('within_group', 'cross_group')),
    estimated_savings    REAL,                       -- null unless OpenCost enabled
    savings_currency     TEXT,
    confidence           TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    summary_text         TEXT
);
CREATE INDEX idx_qos_rec_run ON qos_recommendations (run_id);

CREATE TABLE qos_evidence (
    id                   INTEGER PRIMARY KEY,
    recommendation_id    INTEGER NOT NULL REFERENCES qos_recommendations(id) ON DELETE CASCADE,
    resource             TEXT NOT NULL,
    representative_value REAL,                        -- median utilization (or interaction sum)
    percentile           REAL,                        -- 0..100 within group
    weight               REAL,
    series               TEXT                         -- JSON: optional downsampled series for the chart
);
CREATE INDEX idx_qos_evidence_rec ON qos_evidence (recommendation_id);

CREATE TABLE qos_peers (
    id                INTEGER PRIMARY KEY,
    recommendation_id INTEGER NOT NULL REFERENCES qos_recommendations(id) ON DELETE CASCADE,
    peer_workload_uid TEXT,
    peer_workload     TEXT,
    relation          TEXT,                           -- e.g. "primary upstream"
    affinity          REAL                            -- normalized interaction strength 0..1
);
CREATE INDEX idx_qos_peers_rec ON qos_peers (recommendation_id);
