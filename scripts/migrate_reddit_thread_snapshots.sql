-- 2026-05-05: Reddit per-thread T0/Tn snapshot table for delta-aware search.
--
-- reddit_tools.py:cmd_search upserts one row per thread it sees, keyed by
-- thread_url. On second sight, the helper computes delta_score,
-- delta_comments, and delta_window_min from first_seen_* and surfaces them
-- in the search-result JSON the LLM consumes. That gives Claude a "this
-- thread gained +15 upvotes / +4 comments since I first saw it 32min ago"
-- signal without forcing a full Twitter-style 2-phase staging refactor.
--
-- Lighter than a reddit_candidates pool: no batch IDs, no T1 polling job,
-- no orchestrator changes. Just a side-effect of search() that the same
-- search() call also reads back.

CREATE TABLE IF NOT EXISTS reddit_thread_snapshots (
    thread_url           TEXT PRIMARY KEY,
    subreddit            TEXT,
    title                TEXT,
    first_seen_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    first_seen_score     INTEGER NOT NULL DEFAULT 0,
    first_seen_comments  INTEGER NOT NULL DEFAULT 0,
    last_seen_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_seen_score      INTEGER NOT NULL DEFAULT 0,
    last_seen_comments   INTEGER NOT NULL DEFAULT 0,
    sightings            INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_rts_last_seen
    ON reddit_thread_snapshots(last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_rts_subreddit
    ON reddit_thread_snapshots(subreddit, last_seen_at DESC);
