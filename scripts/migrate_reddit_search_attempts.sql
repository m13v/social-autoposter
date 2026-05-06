-- 2026-05-05: Reddit search-attempt feedback table.
--
-- Mirrors twitter_search_attempts (per-query log, including duds). Today the
-- Reddit pipeline has no record of which queries the LLM actually issued and
-- which ones returned nothing useful, so the next cycle is free to redraft
-- the same dead phrasings indefinitely. This table captures every
-- reddit_tools.py:cmd_search invocation, including zero-result calls.
--
-- One row per (query, subreddits, project) per call. candidates_post_filter = 0
-- marks a dud (post age/locked/archived/blocked filtering already applied).
--
-- Pair with scripts/top_dud_reddit_queries.py which feeds an anti-list into
-- post_reddit.py:build_prompt alongside the existing positive top_search_topics
-- report.

CREATE TABLE IF NOT EXISTS reddit_search_attempts (
    id                      SERIAL PRIMARY KEY,
    query                   TEXT NOT NULL,
    subreddits              TEXT,                       -- comma-separated; NULL = global search
    project_name            TEXT,
    candidates_raw          INTEGER NOT NULL DEFAULT 0, -- pre-filter count from Reddit JSON
    candidates_post_filter  INTEGER NOT NULL DEFAULT 0, -- post age/locked/archived/blocked-sub filter
    top_score               INTEGER NOT NULL DEFAULT 0, -- best upvote count among returned threads
    top_comments            INTEGER NOT NULL DEFAULT 0, -- best comment count among returned threads
    batch_id                TEXT,                       -- groups all queries from one plan-phase Claude session
    ran_at                  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rsa_ran_at
    ON reddit_search_attempts(ran_at DESC);

CREATE INDEX IF NOT EXISTS idx_rsa_dud_lookup
    ON reddit_search_attempts(project_name, candidates_post_filter, ran_at DESC)
    WHERE candidates_post_filter = 0;

CREATE INDEX IF NOT EXISTS idx_rsa_query_proj
    ON reddit_search_attempts(query, project_name, ran_at DESC);
