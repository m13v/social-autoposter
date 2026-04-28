-- 2026-04-28: split human_dm_replies into channels.
-- Until today this table has only ever fed the DM-only flow in
-- engage-dm-replies.sh phase 0, so every existing row is implicitly a DM.
-- Adding the column with NOT NULL DEFAULT 'dm' tags all of them as such.
--
-- reply_channel:
--   'dm'     -> send only as a private DM (legacy behavior, default)
--   'public' -> only post as a public reply on their original comment thread
--   'both'   -> post publicly AND send the DM (paired)
--
-- public_reply_id links the human instruction to the row in `replies` that
-- holds the public-side post (set when phase 0 finishes the public side, so
-- the dashboard can render the public reply distinctly from the DM).

ALTER TABLE human_dm_replies
    ADD COLUMN IF NOT EXISTS reply_channel TEXT NOT NULL DEFAULT 'dm'
        CHECK (reply_channel IN ('dm', 'public', 'both'));

ALTER TABLE human_dm_replies
    ADD COLUMN IF NOT EXISTS public_reply_id INTEGER REFERENCES replies(id);

CREATE INDEX IF NOT EXISTS idx_human_dm_replies_reply_channel
    ON human_dm_replies(reply_channel);
