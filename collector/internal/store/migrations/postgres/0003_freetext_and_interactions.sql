-- 0003_freetext_and_interactions.sql — completes the M1/M2 deferrals (PostgreSQL).
-- Mirrors ../sqlite/0003_*.sql. On Postgres these are simple constraint edits.

-- 1. metric_samples.resource -> free-text (drop the 5-value CHECK).
ALTER TABLE metric_samples DROP CONSTRAINT metric_samples_resource_check;

-- 2. data_sources.type -> add 'interactions'.
ALTER TABLE data_sources DROP CONSTRAINT data_sources_type_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_type_check
    CHECK (type IN ('prometheus', 'custom_api', 'file', 'opencost', 'mesh', 'interactions'));
