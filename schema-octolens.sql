-- schema-octolens.sql -- Octolens webhook mentions table
-- Run once: psql "$DATABASE_URL" -f schema-octolens.sql

CREATE TABLE IF NOT EXISTS octolens_mentions (
    id SERIAL PRIMARY KEY,
    octolens_id BIGINT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    body TEXT,
    author TEXT,
    author_url TEXT,
    author_followers INTEGER DEFAULT 0,
    sentiment TEXT,
    tags TEXT,
    keywords TEXT,
    relevance TEXT,
    source_timestamp TIMESTAMP,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending',
    processed_at TIMESTAMP,
    post_id INTEGER REFERENCES posts(id),
    skip_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_octolens_mentions_status ON octolens_mentions(status);
CREATE INDEX IF NOT EXISTS idx_octolens_mentions_platform ON octolens_mentions(platform);
CREATE INDEX IF NOT EXISTS idx_octolens_mentions_source_ts ON octolens_mentions(source_timestamp);
