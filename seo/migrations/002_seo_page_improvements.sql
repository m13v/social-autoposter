-- Page-improvement pipeline. Every 24h we pick the top-trafficked page for
-- each enabled project, hand it plus its multi-window metrics to a Claude
-- session running inside the repo, and let it make content/layout changes
-- guided by fresh web research. One row per run; metrics are snapshot-at-pick
-- so we can evaluate lift against the next run's numbers.

CREATE TABLE IF NOT EXISTS seo_page_improvements (
    id                    SERIAL PRIMARY KEY,
    product               TEXT NOT NULL,
    domain                TEXT NOT NULL,
    page_path             TEXT NOT NULL,
    page_url              TEXT NOT NULL,

    -- snapshot of funnel metrics at pick time (jsonb so we can add fields
    -- without migrating; each window is {pageviews, email_signups,
    -- schedule_clicks, get_started_clicks, bookings} plus raw totals for
    -- the 7d/30d windows so averaging math is auditable)
    metrics_24h           JSONB NOT NULL,
    metrics_7d_avg        JSONB NOT NULL,
    metrics_30d_avg       JSONB NOT NULL,

    -- Claude session audit trail
    claude_session_id     UUID,
    run_log_path          TEXT,
    brief_json            JSONB,
    tool_summary          JSONB,
    final_result_text     TEXT,

    -- outcome
    commit_sha            TEXT,
    files_modified        TEXT[],
    diff_summary          TEXT,
    rationale             TEXT,
    status                TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','committed','no_change','failed','skipped')),
    error                 TEXT,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at          TIMESTAMPTZ
);

-- Quickly find the last time we touched a given page (debounce + history)
CREATE INDEX IF NOT EXISTS seo_page_improvements_product_path_created
    ON seo_page_improvements (product, page_path, created_at DESC);

-- Throughput view: latest run per product
CREATE INDEX IF NOT EXISTS seo_page_improvements_product_created
    ON seo_page_improvements (product, created_at DESC);
