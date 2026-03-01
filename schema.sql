CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL CHECK(platform IN ('reddit', 'x', 'linkedin')),
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
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'deleted', 'removed')),
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

CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    author TEXT,
    author_handle TEXT,
    title TEXT,
    content TEXT,
    engagement TEXT,
    discovered_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS our_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER REFERENCES threads(id),
    platform TEXT NOT NULL,
    url TEXT,
    content TEXT NOT NULL,
    account TEXT,
    posted_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS thread_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER REFERENCES threads(id),
    author TEXT,
    author_handle TEXT,
    content TEXT,
    engagement TEXT,
    discovered_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','replied','skipped','error')),
    skip_reason TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    replied_at TIMESTAMP
);
