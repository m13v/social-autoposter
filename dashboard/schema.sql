-- Dashboard drafts table — content awaiting client review
-- Run: psql "$DATABASE_URL" -f dashboard/schema.sql

CREATE TABLE IF NOT EXISTS drafts (
    id SERIAL PRIMARY KEY,
    platform TEXT NOT NULL,                -- reddit, twitter, linkedin, moltbook, email
    content_type TEXT NOT NULL DEFAULT 'comment',  -- comment, post, dm, email, reply
    title TEXT,                             -- for posts/emails
    body TEXT NOT NULL,                     -- the draft content
    target_url TEXT,                        -- thread/post URL this responds to
    target_title TEXT,                      -- title of the thread/conversation
    target_author TEXT,                     -- author we're replying to
    target_snippet TEXT,                    -- snippet of what we're replying to
    our_account TEXT,                       -- which account will post this
    project_name TEXT,                      -- which project this is for
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'sent', 'rejected', 'edited')),
    client_note TEXT,                       -- client can leave a note when editing/rejecting
    edited_body TEXT,                       -- client-edited version of body
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP,
    sent_at TIMESTAMP,
    post_id INTEGER,                        -- links to posts.id after sending
    reply_id INTEGER,                       -- links to replies.id after sending
    metadata JSONB DEFAULT '{}'             -- flexible extra data
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_platform ON drafts(platform);
CREATE INDEX IF NOT EXISTS idx_drafts_created_at ON drafts(created_at DESC);
