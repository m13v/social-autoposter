-- 2026-05-01: surface raw pre-floor candidate counts on the dashboard.
--
-- linkedin_search_attempts.candidates_found stores the POST-floor count
-- (candidates that survived discover_linkedin_candidates.py's
-- CONTENT_VIRALITY_FLOOR = 20.0). To distinguish "the SERP returned nothing
-- usable" from "the SERP returned plenty but every card scored under the
-- velocity floor", capture the dropped count separately. Phase A's
-- orchestrator now writes `dropped_below_floor` per query into queries_used,
-- and log_linkedin_search_attempts.py persists it here.
--
-- Default 0 (not NULL) so older queries without the field still render
-- raw = candidates_found + 0 = post-floor. New rows carry the real count.

ALTER TABLE linkedin_search_attempts
    ADD COLUMN IF NOT EXISTS candidates_dropped_below_floor INTEGER NOT NULL DEFAULT 0;
