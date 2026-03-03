-- schema-postgres.sql — Neon Postgres schema mirroring SQLite for SyncField
-- Run once: psql "$DATABASE_URL" -f schema-postgres.sql

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
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
    top_comment_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    platforms TEXT DEFAULT 'x,reddit,moltbook',
    status TEXT DEFAULT 'active',
    max_posts_per_day INTEGER DEFAULT 4,
    posts_made INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY,
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
    replied_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS thread_comments (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER,
    author TEXT,
    author_handle TEXT,
    content TEXT,
    engagement TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS _syncfield_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

INSERT INTO _syncfield_meta (key, value) VALUES ('last_sync', '1970-01-01T00:00:00Z')
ON CONFLICT (key) DO NOTHING;
