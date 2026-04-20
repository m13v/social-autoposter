-- schema-postgres.sql — Neon Postgres schema (primary database)
-- Run once: psql "$DATABASE_URL" -f schema-postgres.sql

CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    thread_url TEXT NOT NULL,
    thread_author TEXT,
    thread_author_handle TEXT,
    thread_title TEXT,
    thread_content TEXT,
    thread_engagement TEXT,
    our_url TEXT,
    our_content TEXT NOT NULL,
    our_account TEXT NOT NULL,
    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active',
    status_checked_at TIMESTAMP,
    engagement_updated_at TIMESTAMP,
    upvotes INTEGER,
    comments_count INTEGER,
    views INTEGER,
    source_turn_id INTEGER,
    source_summary TEXT,
    top_comment_author TEXT,
    top_comment_content TEXT,
    top_comment_upvotes INTEGER,
    top_comment_url TEXT,
    link_edited_at TIMESTAMP,
    link_edit_content TEXT
);

-- Add columns to existing deployments (safe to re-run)
ALTER TABLE posts ADD COLUMN IF NOT EXISTS link_edited_at TIMESTAMP;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS link_edit_content TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS scan_no_change_count INTEGER DEFAULT 0;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS project_name TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS feedback_report_used BOOLEAN DEFAULT FALSE;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS engagement_style TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS resurrected_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
CREATE INDEX IF NOT EXISTS idx_posts_resurrected_at ON posts(resurrected_at) WHERE resurrected_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS threads (
    id SERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    author TEXT,
    author_handle TEXT,
    title TEXT,
    content TEXT,
    engagement TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS our_posts (
    id SERIAL PRIMARY KEY,
    thread_id INTEGER REFERENCES threads(id),
    platform TEXT NOT NULL,
    url TEXT,
    content TEXT NOT NULL,
    account TEXT,
    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaigns (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    platforms TEXT DEFAULT 'twitter,reddit,moltbook',
    status TEXT DEFAULT 'active',
    max_posts_per_day INTEGER DEFAULT 4,
    max_posts_total INTEGER,
    posts_made INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS max_posts_total INTEGER;

CREATE TABLE IF NOT EXISTS post_campaigns (
    post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    attached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (post_id, campaign_id)
);

CREATE INDEX IF NOT EXISTS idx_post_campaigns_campaign ON post_campaigns(campaign_id);

CREATE TABLE IF NOT EXISTS replies (
    id SERIAL PRIMARY KEY,
    post_id INTEGER REFERENCES posts(id),
    platform TEXT NOT NULL,
    their_comment_id TEXT NOT NULL,
    their_author TEXT,
    their_content TEXT,
    their_comment_url TEXT,
    our_reply_id TEXT,
    our_reply_content TEXT,
    our_reply_url TEXT,
    parent_reply_id INTEGER REFERENCES replies(id),
    moltbook_post_uuid TEXT,
    moltbook_parent_comment_uuid TEXT,
    depth INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',
    skip_reason TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processing_at TIMESTAMP,
    replied_at TIMESTAMP,
    CONSTRAINT replies_platform_comment_id_unique UNIQUE (platform, their_comment_id)
);

-- Add columns to existing deployments (safe to re-run)
ALTER TABLE replies ADD COLUMN IF NOT EXISTS processing_at TIMESTAMP;
ALTER TABLE replies ADD COLUMN IF NOT EXISTS engagement_style TEXT;
ALTER TABLE replies ADD CONSTRAINT IF NOT EXISTS replies_platform_comment_id_unique UNIQUE (platform, their_comment_id);

CREATE TABLE IF NOT EXISTS dms (
    id SERIAL PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'reddit',
    reply_id INTEGER REFERENCES replies(id),
    post_id INTEGER REFERENCES posts(id),
    their_author TEXT NOT NULL,
    their_content TEXT,
    our_dm_content TEXT,
    comment_context TEXT,
    status TEXT DEFAULT 'pending',
    skip_reason TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    CONSTRAINT dms_platform_author_reply_unique UNIQUE (platform, their_author, reply_id)
);

CREATE INDEX IF NOT EXISTS idx_dms_status ON dms(status);
CREATE INDEX IF NOT EXISTS idx_dms_their_author ON dms(their_author);

-- Evolve dms into conversation headers
ALTER TABLE dms ADD COLUMN IF NOT EXISTS chat_url TEXT;
ALTER TABLE dms ADD COLUMN IF NOT EXISTS conversation_status TEXT DEFAULT 'active';
ALTER TABLE dms ADD COLUMN IF NOT EXISTS tier INTEGER DEFAULT 1;
ALTER TABLE dms ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMP;
ALTER TABLE dms ADD COLUMN IF NOT EXISTS message_count INTEGER DEFAULT 0;
ALTER TABLE dms ADD COLUMN IF NOT EXISTS interest_level TEXT;  -- no_response | general_discussion | cold | warm | hot | declined | not_our_prospect

-- Qualification + book-a-call conversion flow
ALTER TABLE dms ADD COLUMN IF NOT EXISTS target_project TEXT;              -- project we are pursuing for this thread (set at outreach)
ALTER TABLE dms ADD COLUMN IF NOT EXISTS qualification_status TEXT DEFAULT 'pending';  -- pending | asked | answered | qualified | disqualified
ALTER TABLE dms ADD COLUMN IF NOT EXISTS qualification_notes TEXT;
ALTER TABLE dms ADD COLUMN IF NOT EXISTS booking_link_sent_at TIMESTAMP;
ALTER TABLE dms ADD COLUMN IF NOT EXISTS icp_precheck TEXT;                -- pass | fail | ambiguous (labelled at outreach, not used as filter)
ALTER TABLE dms ADD COLUMN IF NOT EXISTS prospect_id INTEGER;              -- FK added below after prospects table defined

-- prospects: persistent per-(platform, author) record. One person can have multiple DMs over time.
CREATE TABLE IF NOT EXISTS prospects (
    id SERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    author TEXT NOT NULL,
    profile_url TEXT,
    display_name TEXT,
    headline TEXT,
    bio TEXT,
    follower_count INTEGER,
    recent_activity TEXT,
    company TEXT,
    role TEXT,
    profile_fetched_at TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT prospects_platform_author_unique UNIQUE (platform, author)
);

CREATE INDEX IF NOT EXISTS idx_prospects_platform_author ON prospects(platform, author);
CREATE INDEX IF NOT EXISTS idx_prospects_profile_fetched ON prospects(profile_fetched_at);

-- dms.prospect_id FK (added after prospects table exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'dms_prospect_id_fkey' AND table_name = 'dms'
    ) THEN
        ALTER TABLE dms ADD CONSTRAINT dms_prospect_id_fkey FOREIGN KEY (prospect_id) REFERENCES prospects(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_dms_prospect_id ON dms(prospect_id);
CREATE INDEX IF NOT EXISTS idx_dms_target_project ON dms(target_project);
CREATE INDEX IF NOT EXISTS idx_dms_qualification_status ON dms(qualification_status);

-- dm_messages: every message in a DM conversation (ours and theirs)
CREATE TABLE IF NOT EXISTS dm_messages (
    id SERIAL PRIMARY KEY,
    dm_id INTEGER NOT NULL REFERENCES dms(id),
    direction TEXT NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    message_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dm_messages_dm_id ON dm_messages(dm_id);
CREATE INDEX IF NOT EXISTS idx_dm_messages_direction ON dm_messages(direction);

CREATE TABLE IF NOT EXISTS thread_comments (
    id SERIAL PRIMARY KEY,
    thread_id INTEGER,
    author TEXT,
    author_handle TEXT,
    content TEXT,
    engagement TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- claude_sessions: one row per `claude -p` invocation in a runner script.
-- Activity rows in posts/replies/dms reference session_id; cost is split
-- evenly across all activities sharing the same session at query time.
CREATE TABLE IF NOT EXISTS claude_sessions (
    session_id UUID PRIMARY KEY,
    script TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    duration_ms BIGINT,
    total_cost_usd NUMERIC(10, 6),
    input_tokens BIGINT,
    output_tokens BIGINT,
    cache_read_tokens BIGINT,
    cache_creation_tokens BIGINT,
    model_breakdown JSONB,
    logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_claude_sessions_started ON claude_sessions(started_at DESC);

ALTER TABLE posts        ADD COLUMN IF NOT EXISTS claude_session_id UUID;
ALTER TABLE replies      ADD COLUMN IF NOT EXISTS claude_session_id UUID;
ALTER TABLE dms          ADD COLUMN IF NOT EXISTS claude_session_id UUID;
ALTER TABLE dm_messages  ADD COLUMN IF NOT EXISTS claude_session_id UUID;

CREATE INDEX IF NOT EXISTS idx_posts_claude_session       ON posts(claude_session_id)       WHERE claude_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_replies_claude_session     ON replies(claude_session_id)     WHERE claude_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dms_claude_session         ON dms(claude_session_id)         WHERE claude_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dm_messages_claude_session ON dm_messages(claude_session_id) WHERE claude_session_id IS NOT NULL;

