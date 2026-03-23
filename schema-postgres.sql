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

CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);

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
    posts_made INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

