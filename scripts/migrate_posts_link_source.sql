-- Adds posts.link_source so the Twitter page-gen A/B can be analysed after
-- the fact: seo_page (gen lane succeeded) vs plain_url_ab_skip (lost the
-- coin flip) vs plain_url_no_lp (project ineligible) vs
-- plain_url_fallback:<reason> (gen attempted + failed).
ALTER TABLE posts ADD COLUMN IF NOT EXISTS link_source TEXT;
