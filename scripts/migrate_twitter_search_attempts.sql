-- 2026-04-29: track every Twitter search query the LLM drafted, including the
-- ones that returned ZERO tweets, so the next cycle can be told "do not redraft
-- these — they have been flat for the last 24-48h".
--
-- Why a separate table from twitter_candidates: candidates only have rows for
-- tweets that were actually scraped. A query that returned "No results" leaves
-- no trace there, so the LLM was free to reissue the same dud phrasing every
-- 20 minutes. This table captures the attempt itself.
--
-- One row per (query, project) per cycle. tweets_found = 0 marks a dud.
-- Pair with scripts/top_dud_twitter_queries.py to feed an anti-list into the
-- run-twitter-cycle.sh prompt alongside top_twitter_queries.py.

CREATE TABLE IF NOT EXISTS twitter_search_attempts (
    id            SERIAL PRIMARY KEY,
    query         TEXT NOT NULL,
    project_name  TEXT,
    tweets_found  INTEGER NOT NULL DEFAULT 0,
    batch_id      TEXT,
    ran_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tsa_ran_at
    ON twitter_search_attempts(ran_at DESC);

CREATE INDEX IF NOT EXISTS idx_tsa_dud_lookup
    ON twitter_search_attempts(project_name, tweets_found, ran_at DESC)
    WHERE tweets_found = 0;
