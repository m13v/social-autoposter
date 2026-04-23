-- Add per-row Claude model stamp to every SEO pipeline table that already
-- tracks claude_session_id. Backfill is handled by scripts/log_claude_session.py
-- after each session ends, joining on claude_session_id and writing the
-- dominant model id (max output_tokens from model_breakdown).

ALTER TABLE seo_escalations       ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE seo_keywords          ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE seo_page_improvements ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE gsc_queries           ADD COLUMN IF NOT EXISTS model TEXT;
