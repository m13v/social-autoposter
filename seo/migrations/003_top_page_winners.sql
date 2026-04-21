-- Top-pages cross-project replication pipeline (run_top_pages_pipeline.sh)
-- records the seed page it picked as the global winner each day, so the
-- picker can enforce a per-(product, path) cooldown and rotate winners
-- across days instead of repeatedly reseeding from the same page.

CREATE TABLE IF NOT EXISTS top_page_winners (
    id         SERIAL PRIMARY KEY,
    product    TEXT NOT NULL,
    path       TEXT NOT NULL,
    page_url   TEXT,
    score      NUMERIC,
    metrics    JSONB,
    won_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS top_page_winners_product_path_won
    ON top_page_winners (product, path, won_at DESC);

CREATE INDEX IF NOT EXISTS top_page_winners_won_at
    ON top_page_winners (won_at DESC);
