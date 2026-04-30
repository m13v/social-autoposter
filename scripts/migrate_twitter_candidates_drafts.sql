-- 2026-04-29: persist Phase 2b drafts on twitter_candidates so failed CDP /
-- posting runs don't waste the LLM redraft cost on the next cycle.
--
-- A pending candidate may carry draft_reply_text from a prior cycle's Phase 2b
-- that wrote the draft but never reached the post step (CDP timeout, browser
-- crash, monthly cap, etc.). The next cycle's Phase 2b posts the existing draft
-- as-is when it's still fresh (DRAFT_TTL); otherwise it redrafts.
--
-- Salvage (Phase 0 carrying pending rows forward) and drafts compose: salvage
-- moves the row to the new batch_id; drafts let Phase 2b skip the LLM step.
-- Together they make "pending" mean "still owes a post; possibly with text
-- already written."

ALTER TABLE twitter_candidates
    ADD COLUMN IF NOT EXISTS draft_reply_text       TEXT,
    ADD COLUMN IF NOT EXISTS draft_engagement_style TEXT,
    ADD COLUMN IF NOT EXISTS drafted_at             TIMESTAMP WITH TIME ZONE;

-- Index lets the dashboard / log_run queries find drafts cheaply without a
-- sequential scan on the (now wider) candidates table.
CREATE INDEX IF NOT EXISTS idx_tc_drafted_at
    ON twitter_candidates(drafted_at)
    WHERE drafted_at IS NOT NULL;
