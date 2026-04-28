-- Per-reply engagement stats columns (added 2026-04-28)
-- Mirrors posts.upvotes/comments_count/views/engagement_updated_at on replies.
ALTER TABLE replies ADD COLUMN IF NOT EXISTS upvotes INTEGER DEFAULT 0;
ALTER TABLE replies ADD COLUMN IF NOT EXISTS comments_count INTEGER DEFAULT 0;
ALTER TABLE replies ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0;
ALTER TABLE replies ADD COLUMN IF NOT EXISTS engagement_updated_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_replies_engagement_updated_at ON replies(engagement_updated_at);

-- Verify
\echo '-- replies columns after migration:'
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name='replies'
  AND column_name IN ('upvotes','comments_count','views','engagement_updated_at')
ORDER BY column_name;
