# Jungle AI (jungleai.com) — Ranking Analysis

**Date:** 2026-04-24
**SEO Health Score:** 56/100 (mid-tier; strong product, leaky technical/E-E-A-T foundation)

> Note: the `?ws=...` param in the URL originally shared is a workspace ID; canonical is `https://jungleai.com/`. Verified it doesn't create duplicate-content (canonical points to `/`).

## SERP & Traffic Snapshot (March 2026)

| Metric | SimilarWeb | Semrush |
|---|---|---|
| Monthly visits | ~364K (3-mo avg, -9.85% MoM) | ~163K (+5.17% MoM) |
| Global rank | #90,781 | #226,182 |
| Bounce rate | 32.5% (great) | 50.7% (avg) |
| Pages / visit | 6.72 | 4.77 |
| Avg session | 3:17 | 6:06 |
| Organic share of traffic | ~50% | ~36% |
| Organic vs paid | 95% / 5% | — |
| Authority Score (Semrush) | — | 39 |
| Backlinks | — | 24.6K |
| Referring domains | — | 1.73K (+12% MoM) |
| AI traffic (ChatGPT) | — | 167 visits (0.1%) |

**Top countries:** US (16-20%), Australia (25% per Semrush; likely founder geo skew), Costa Rica (6%), Brazil (5%), Mexico (5%), India (7%).
**Demographics:** 68% female, age 18-24 (consistent with student persona).

**Top organic keywords (real ranking traffic):**

1. `jungle` — pos 2, ~8,100/mo, 42% of organic
2. `jungle ai` — pos 1, ~880/mo, 28%
3. `jungleai` — pos 1, ~260/mo, 8%
4. `flashcard maker` — top 10, traffic share present

**The brand-keyword problem:** ~78% of organic comes from `jungle*` brand queries. They are NOT yet ranking meaningfully for the high-intent generic queries (`AI flashcard maker`, `MCQ generator from PDF`, `Quizlet alternative`) where the volume is. That's the headroom.

**Competitors taking that volume:** gizmo.ai (3.38M visits), noji.io (547K), flashcards.world (314K). Jungle is 5-10x smaller than the category leaders despite a great product.

---

## What's Helping Them Rank

- Brand domination (#1 for "jungle ai", #2 for "jungle", beating the actual jungle)
- Excellent `/llms.txt` (named founders, comparison matrix, keyword bank; doing 60% of their AI visibility work)
- HTTPS + HSTS, robots.txt + sitemap clean, Spanish hreflang correct
- Framer SSGs visible copy into HTML (Googlebot sees content without JS)
- Bounce rate 32% + 6.7 pages/visit = strong engagement signals
- Comparison-page slugs that match AI-search queries (`/alternatives/quizlet-alternative`, `/anki-vs-jungle`)
- 16,773-word USMLE pillar posts (good topical depth)

## What's Hurting Their Rankings

### Critical (fix this week)

1. **Zero `<h1>` on every commercial page** — homepage, pricing, all 4 use-case pages, alternatives. Foundational on-page failure across the entire money funnel.
2. **4 of their own short URLs are 404s**: `/ai-mcq-maker`, `/ai-quiz-maker`, `/ai-image-occlusion`, `/ai-flashcard-maker`. Real pages live at `/use-cases/...`. Add 301s or publish at the short URLs.
3. **Soft-404**: the 404 template returns HTTP 200 with the homepage title. Google will see thousands of "real" pages that are actually missing.
4. **Zero JSON-LD structured data sitewide** (no Organization, SoftwareApplication, BlogPosting, Product, FAQPage). Biggest single fix for AI Overviews + Bing Copilot.
5. **No `/contact` page, no real contact info anywhere.** Direct E-E-A-T trust failure under Google's QRG.
6. **USMLE blog posts (medical/YMYL) have no author byline, no medical reviewer, no visible date** despite making clinical claims. High-risk for YMYL ranking suppression.

### High

7. **Sitemap pollution**: indexes `/home-2`, `/home-3`, `/Deutsch`, `/ethan`, `/case`, `/upgrade`, `/mobile`, `/ucla`. Drafts/internal pages that should be `noindex`.
8. **Only 2 internal `<a href>` links in homepage HTML source.** Nav is JS-only Framer components. Throttles link-equity flow and Bing/non-Google crawl.
9. **Title casing**: lowercase ("the best free anki alternative - jungle vs anki") plus a misspelling ("quizes" instead of "quizzes"). Hurts SERP CTR + brand trust.
10. **Comparison pages exist but render as fragmented Framer divs**: no `<table>`, no FAQ blocks, duplicated phrases. LLMs can't extract a clean "Jungle vs Quizlet" passage to quote.
11. **Missing high-value comparison pages**: Knowt, RemNote, NotebookLM, StudyFetch, Gizmo, Wisdolia, Unstuck.
12. **Entity disambiguation risk**: collides with "Jungle.ai" (the industrial-AI company). No Wikipedia entity. LLMs may conflate them.
13. **Privacy Policy stale** (June 2023, ~3 yrs old).

### Medium

14. Viewport meta missing `initial-scale=1`.
15. Page weight 448KB HTML on homepage; LCP image preloaded at 2048w even on mobile.
16. Analytics (GTM/VWO/Amplitude) gated behind `4G + 8GB RAM`. Most mobile users don't qualify, so AB-test data is unreliable.
17. `/llms-full.txt` returns 404 (gap; companion to the strong `/llms.txt`).
18. Use-case pages (~10K words each) duplicate ~half their H2s ("upload your study materials" rendered twice from mobile/desktop variants).

---

## Top 7 Ranking Wins, Ordered by ROI

| # | Action | Effort | Impact |
|---|---|---|---|
| 1 | Add proper `<h1>` to homepage + all `/use-cases/*` + `/pricing` + `/alternatives/*` (Framer: change tag from div to H1) | 1 hr | High; foundational on-page |
| 2 | Add `Organization` + `SoftwareApplication` + `BlogPosting` + `FAQPage` JSON-LD via Framer custom code | 1 day | High; rich results + AI citation |
| 3 | Fix the four 404'd short URLs + soft-404 template | 2 hr | High; direct ranking + crawl-budget |
| 4 | Ship a real `/contact` page; add author bios + medical reviewer + dates to USMLE posts | 1 day | High; E-E-A-T trust |
| 5 | Rewrite comparison pages with real `<table>` + 5-7 H2-tagged Q&A blocks (134-167 words each) | 3-4 days | High; AI search citability |
| 6 | Publish missing alternatives pages: Knowt, RemNote, NotebookLM, StudyFetch, Gizmo, Wisdolia | 1 day each | Med-High; captures "best X alternative" queries |
| 7 | Trim sitemap (kill `/home-2`, `/Deutsch`, `/ucla` etc.) + add `/llms-full.txt` | 2 hr | Med; crawl-budget cleanup |

**The bigger story:** Jungle is winning brand-search and engagement (32% bounce, 6.7 pages/visit is genuinely great), but is bleeding category-search opportunity. Competitors at 3-10× their traffic occupy the generic flashcard/quiz/MCQ queries because Jungle's commercial pages have no H1, no schema, and weak comparison content. Fixing items 1-5 above is the realistic path to 2-3× organic traffic over 90 days.

---

## Per-Subagent Detail

### Technical SEO (Score: 62/100)

| Category | Status | Notes |
|---|---|---|
| Crawlability | PASS | robots.txt allows all, sitemap declared |
| Indexability | PARTIAL | canonical OK, but `?ws=` not normalized |
| Security (HTTPS/HSTS) | PASS | HSTS 1yr, HTTP/308, www/308 |
| URL structure | PARTIAL | trailing slashes 308 to non-slash (good); 4 known URLs return 404 |
| Mobile | WEAK | viewport missing `initial-scale=1` |
| Core Web Vitals proxy | FAIL | 448 KB HTML, conditional GTM/VWO, Framer hydration |
| Structured data | FAIL | none detected on homepage |
| JS rendering | PASS-ish | Framer SSGs visible text into HTML |
| Internal linking | FAIL | only 2 unique internal links from homepage source |
| Hreflang | PASS | en/es/x-default declared correctly |

### Content & E-E-A-T (Score: 58/100, AI Citation Readiness: 42/100)

| Factor | Score | Notes |
|---|---|---|
| Experience (20%) | 12/20 | "1 million+ students" claim, testimonials present, but no first-person case studies |
| Expertise (25%) | 10/25 | USMLE blog content lacks medical author/reviewer attribution |
| Authoritativeness (25%) | 13/25 | "loved by students at..." school logos exist; no press, no .edu citations |
| Trustworthiness (30%) | 15/30 | Privacy/ToS exist but at non-obvious URLs; no contact page |

### Schema Markup (Score: 0/100)

Zero structured data sitewide. Framer-built site with only Open Graph + standard meta tags.
Top 3 missing: `Organization` + `WebSite`/`SearchAction` (homepage), `SoftwareApplication` (homepage + use-case pages), `Product` + `Offer` (`/pricing`).
Implementation: Framer supports custom `<head>` HTML per page (Site Settings -> General -> Custom Code, and per-page SEO panel).

### AI Search / GEO (Score: 58/100)

| Dimension | Score | Notes |
|---|---|---|
| Citability | 12/25 | comparison pages exist but render Framer-thin, no FAQ blocks |
| Structural Readability | 10/20 | zero JSON-LD across home + comparison pages |
| Multi-Modal | 9/15 | image-heavy with thin alt context for LLMs |
| Authority & Brand | 12/20 | founders named in llms.txt, but unverified entity (no Wikipedia, weak Reddit footprint) |
| Technical Accessibility | 15/20 | robots.txt fully open, llms.txt excellent |

**Platform visibility estimate:** Google AI Overviews 35/100, ChatGPT 65/100, Perplexity 55/100, Bing Copilot 30/100.

---

## Sources

- [Similarweb traffic profile](https://www.similarweb.com/website/jungleai.com/)
- [Semrush traffic overview](https://www.semrush.com/website/jungleai.com/overview/)
- [Futurepedia listing](https://www.futurepedia.io/tool/jungleai)
- [Toolify listing](https://www.toolify.ai/tool/jungle-ai/)
