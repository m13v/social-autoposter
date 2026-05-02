-- 2026-05-01: phase-aware salvage for run-twitter-cycle.sh
--
-- Adds a per-cycle phase tracking row so Phase 0 of subsequent cycles can
-- decide salvage timing per-phase instead of using a flat 20-min wall-clock
-- cutoff. The flat cutoff was salvaging live cycles whose Phase 2b-gen step
-- (SEO landing-page generation, observed 10-40 min) hadn''t finished, leading
-- to phantom "failed=1" rows and double-prep cost (cycle 16:23 -> 16:53 race
-- on candidate 7994, 2026-05-01).
--
-- See run-twitter-cycle.sh Phase 0 SQL for the consumer.
-- See scripts/twitter_batch_phase.py for the start/advance/end helper.

CREATE TABLE IF NOT EXISTS twitter_batches (
    batch_id          TEXT PRIMARY KEY,
    owner_pid         INTEGER,
    owner_host        TEXT,
    current_phase     TEXT,                       -- phase0 | phase1 | phase2a | phase2b-prep | phase2b-gen | phase2b-post
    phase_started_at  TIMESTAMPTZ DEFAULT NOW(),
    started_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_twitter_batches_phase_started_at
    ON twitter_batches (phase_started_at);
