-- SEO escalation rail. Mirrors the DM escalation pattern in
-- scripts/dm_conversation.py + scripts/ingest_human_dm_replies.py.
--
-- Lifecycle:
--   1. generate_page.py (or the setup gate) calls seo/escalate.py open
--      which inserts a row with status='pending' and emails i@m13v.com.
--   2. Human replies in Gmail; ingest_human_seo_replies.py picks up the
--      Re: [SEO #N] thread, writes human_reply, flips status='replied'.
--   3. resume_escalations.py at top of cron_seo.sh re-invokes
--      generate_page.py --resume-escalation N, which prepends the human
--      reply into the prompt and on success flips status='resumed'.
--   4. status='cancelled' is for manual closure (CLI), no resume.

CREATE TABLE IF NOT EXISTS seo_escalations (
    id                    SERIAL PRIMARY KEY,
    source_table          TEXT NOT NULL CHECK (source_table IN ('seo_keywords','gsc_queries')),
    source_id             INT,
    product               TEXT NOT NULL,
    keyword               TEXT NOT NULL,
    slug                  TEXT,
    claude_session_id     UUID,
    run_log_path          TEXT,
    reason                TEXT NOT NULL,
    trigger_kind          TEXT NOT NULL CHECK (trigger_kind IN ('model_initiated','setup_gate','reaper_stuck')),
    asked_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    gmail_outbound_id     TEXT,
    status                TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','replied','resumed','cancelled')),
    human_reply           TEXT,
    replied_at            TIMESTAMPTZ,
    gmail_inbound_id      TEXT UNIQUE,
    resumed_at            TIMESTAMPTZ,
    resumed_run_log_path  TEXT,
    resume_outcome        TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One open escalation per (product, keyword) at a time. Doubles as the 24h
-- debounce: caller must check for an existing pending row, or rely on this
-- index to reject the second INSERT. We do explicit dedupe in escalate.py
-- with a 24h window so we can re-escalate after a day without intervention.
CREATE UNIQUE INDEX IF NOT EXISTS seo_escalations_unique_open
    ON seo_escalations (product, keyword)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS seo_escalations_status_asked_at
    ON seo_escalations (status, asked_at DESC);

CREATE INDEX IF NOT EXISTS seo_escalations_product_status
    ON seo_escalations (product, status);

-- Forward link from the keyword/query row to its open escalation. NULL when
-- no escalation is open. Cleared when status flips to resumed/cancelled.
ALTER TABLE seo_keywords
    ADD COLUMN IF NOT EXISTS open_escalation_id INT
        REFERENCES seo_escalations(id) ON DELETE SET NULL;

ALTER TABLE gsc_queries
    ADD COLUMN IF NOT EXISTS open_escalation_id INT
        REFERENCES seo_escalations(id) ON DELETE SET NULL;

-- New status value 'escalated' is just a TEXT value; no enum to alter.
-- Existing CHECK constraints (if any) on seo_keywords.status are not in the
-- live schema (verified via information_schema), so no constraint edit needed.
