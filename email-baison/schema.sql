-- email-baison/schema.sql — EmailBison reply tracking tables
-- Run once: psql "$DATABASE_URL" -f email-baison/schema.sql

CREATE TABLE IF NOT EXISTS email_replies (
    id SERIAL PRIMARY KEY,
    bison_reply_id TEXT NOT NULL UNIQUE,
    campaign_id TEXT,
    campaign_name TEXT,
    sequence_id TEXT,
    from_email TEXT NOT NULL,
    from_name TEXT,
    to_email TEXT NOT NULL,
    subject TEXT,
    body_text TEXT,
    body_html TEXT,
    received_at TIMESTAMP,
    interest_status TEXT DEFAULT 'unknown',
    our_draft TEXT,
    our_reply_sent BOOLEAN DEFAULT FALSE,
    our_reply_sent_at TIMESTAMP,
    project_name TEXT,
    status TEXT DEFAULT 'pending',
    skip_reason TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_email_replies_status ON email_replies(status);
CREATE INDEX IF NOT EXISTS idx_email_replies_campaign ON email_replies(campaign_id);
CREATE INDEX IF NOT EXISTS idx_email_replies_from ON email_replies(from_email);
