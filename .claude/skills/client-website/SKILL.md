---
name: client-website
description: "End-to-end workflow for creating, rebuilding, or enhancing a client's website. Covers SEO audit, content extraction, Next.js scaffolding, real image/video assets, structured data, SEO guide pages, component injection, analytics, deployment, and dashboard registration. Includes concrete design system blueprints with exact Tailwind classes, component templates, and section layouts. Use when: 'create client website', 'rebuild website', 'recreate site', 'client landing page', 'SEO pages for client', or when onboarding a new client who needs a web presence."
user_invocable: true
---

# Client Website

End-to-end workflow for building a client's website from scratch or recreating/improving an existing one. Produces a modern, SEO-optimized Next.js site with real content, images, video embeds, structured data, and programmatic SEO guide pages.

## Arguments

Provide the client name, domain (if any), and existing site URL (if any). Example: `"Paperback Expert at paperbackexpert.com"`

## Prerequisites

- **Vercel account** with configured scope/team
- **GitHub** org or personal account
- **PostHog** account for analytics
- **Google Search Console** access
- **Isolated browser MCP** for visual comparison

## Stack

- Next.js 16 (App Router) + React 19 + TypeScript
- Tailwind CSS 4 (inline theme via `@theme`)
- next/image for optimized images
- Vercel for hosting
- PostHog for analytics (CTA clicks, pageviews)

---

## Phase 1: Audit and Research

### 1a. SEO Audit (if existing site)

Run parallel SEO agents to baseline the current site:

```
Launch 5 agents in parallel:
- seo-technical: crawlability, indexability, Core Web Vitals, mobile
- seo-content: E-E-A-T signals, readability, content depth
- seo-schema: existing structured data (JSON-LD, Microdata, RDFa)
- seo-performance: Lighthouse scores, LCP, CLS, TBT (desktop + mobile)
- seo-geo: AI crawler accessibility, llms.txt, citation readiness
```

Record all scores. These become the "before" baseline and the fix list for the new site.

### 1b. Crawl All Pages

Use an agent with WebFetch to discover and extract content from every page on the site:

1. Fetch the homepage, extract all navigation and footer links
2. Try common paths: /about, /services, /contact, /faq, /blog, /pricing, /testimonials, /privacy
3. For each discovered page, extract: URL, page title, all headings with hierarchy, full body text, CTA text and link targets, form fields, navigation links

**Output:** Complete content inventory organized by page.

### 1c. Extract Visual Assets

Use the isolated browser to catalog images, videos, and embeds. Download all identified images to `public/images/` with descriptive filenames.

**Key assets to identify:** Logo, hero images, client/team headshots, product images, social proof imagery, video embeds (Vimeo, YouTube), scheduling widgets (Calendly, Cal.com).

### 1d. Take Full Page Screenshots

Capture full-page screenshots of every key page on the original site for visual reference. Save as `original-{pagename}-full.png`.

---

## Phase 2: Scaffold Project

### 2a. Create Next.js App

```bash
cd ~
npx create-next-app@latest CLIENT-website --typescript --tailwind --eslint --app --src-dir --no-turbopack --import-alias "@/*"
```

### 2b. Configure Theme (globals.css)

The theme uses CSS custom properties in `:root` mapped into Tailwind 4 via `@theme inline`. Always define both the CSS variable AND the Tailwind mapping. Every client gets a primary color, a dark variant, and an accent color at minimum.

```css
@import "tailwindcss";

:root {
  --background: #ffffff;
  --foreground: #1a1a1a;
  --primary: #073c61;
  --primary-dark: #052d49;
  --cta: #e11010;
  --cta-dark: #ae0c0c;
  --accent: #d4a843;
  --accent-light: #f0d88a;
}

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-primary: var(--primary);
  --color-primary-dark: var(--primary-dark);
  --color-cta: var(--cta);
  --color-cta-dark: var(--cta-dark);
  --color-accent: var(--accent);
  --color-accent-light: var(--accent-light);
  --font-sans: var(--font-inter);
  --font-heading: var(--font-oswald);
}
```

### 2c. Configure Fonts (layout.tsx)

Use `next/font/google` with CSS variable mode. Apply both variables to the `<html>` tag.

**IMPORTANT: Route group architecture.** The root layout must NOT include Header/Footer directly. Use a `(main)` route group with its own layout for pages that need the site Header/Footer.

- **Root layout (`src/app/layout.tsx`):** fonts, metadata, Organization JSON-LD, and `{children}` only.
- **Main layout (`src/app/(main)/layout.tsx`):** wraps all pages with Header/Footer.

All page routes (homepage, about, wins, faq, precall, AND `/t/` guide pages) go inside `src/app/(main)/`.

See [references/DESIGN-SYSTEM.md](references/DESIGN-SYSTEM.md) for font pairing guide.

---

## Phase 3: Build All Pages

For exact component blueprints, Tailwind classes, and section patterns, see:
- **[references/COMPONENTS.md](references/COMPONENTS.md)** — Header, Footer, FAQ accordion, case study cards, precall page, about page, homepage sections, SVG icons, embed patterns
- **[references/DESIGN-SYSTEM.md](references/DESIGN-SYSTEM.md)** — Typography classes, color usage, image integration checklist

### Page build order

1. **Header** — Sticky nav with logo, dropdown menus, mobile hamburger, CTA button
2. **Footer** — 4-column layout: brand, company links, resource links, CTA + contact
3. **Homepage** — Hero, product strip, stats bar, benefits grid (3-col), process steps (4-col), testimonials (glass cards on dark bg), final CTA
4. **Inner pages** — About, Wins/Case Studies, How It Works, FAQ (accordion), Precall (video + Calendly + testimonial sidebar), Blog, Privacy Policy
5. **SEO guide pages** — Under `/t/{slug}`, inside `(main)` route group

### Structured Data

Every page gets JSON-LD. Minimum set:
- `layout.tsx`: Organization (site-wide)
- Each page: BreadcrumbList
- Wins page: Review for each case study
- FAQ page: FAQPage
- Homepage: WebPage

### SEO Infrastructure

Create `src/app/sitemap.ts` (priorities: 1.0 homepage, 0.9 core conversion, 0.8 secondary, 0.6 resource, 0.3 legal) and `src/app/robots.ts`.

---

## Phase 4: Programmatic SEO Guide Pages

### 4a. Create Shared Guide Components

Create in `src/components/`: GuideCTASection, InlineCTA, StickyBottomCTA, ProofBanner.

### 4b. Create SEO Guide Pages

**MANDATORY:** Follow the `seo-page-ui` skill (`~/social-autoposter/.claude/skills/seo-page-ui/SKILL.md`) for the page structure.

Create guide pages at `src/app/(main)/t/{slug}/page.tsx`. They live inside the `(main)` route group so they automatically get the site Header/Footer.

**Content requirements:** 2,000+ words, real statistics with sources, practical frameworks, natural mention of client's service, proper heading hierarchy, table of contents with anchor links, metadata with OG tags.

### 4c. Transform Script

Create `~/social-autoposter/scripts/transform_{client}_pages.mjs` that injects StickyBottomCTA, ProofBanner, InlineCTA, and GuideCTASection into guide pages.

---

## Phase 5: Build and Verify

### 5a. Build

```bash
cd ~/CLIENT-website && npm run build
```

Fix any TypeScript or build errors.

### 5b. Visual Comparison

Use the isolated browser to compare new site screenshots against originals from Phase 1d. Check for: real logo, all photos present, video embeds working, scheduling widget embedded, social icons in footer, correct color scheme, mobile responsive (375px width).

---

## Phase 6: Deploy

```bash
cd ~/CLIENT-website
git init && git add -A
git commit -m "Initial commit: CLIENT_NAME website"
gh repo create m13v/CLIENT-website --private --source=. --push
vercel --yes
vercel --prod --yes
```

Set environment variables for PostHog if needed. Add custom domain when ready via `vercel domains add`.

---

## Phase 7: Register for Tracking

### 7a. Add to SEO Pages Dashboard

Edit `~/social-autoposter-website/src/app/api/dashboard/seo-pages/route.ts` and add the new client to `CLIENT_SEO_CONFIG`.

### 7b. Google Search Console

Add property, verify via TXT record, submit sitemap.

---

## Phase 8: Final Verification Checklist

- [ ] Site loads at production URL
- [ ] All pages render with real content (no placeholder text)
- [ ] All images load (no broken images)
- [ ] Logo appears in header and footer
- [ ] Navigation dropdowns work on desktop and mobile
- [ ] Video embeds play
- [ ] Scheduling widget loads
- [ ] Social media icons present in footer
- [ ] JSON-LD structured data on every page
- [ ] Sitemap accessible at /sitemap.xml
- [ ] Lighthouse desktop score >= 85, mobile >= 70
- [ ] No console errors on any page
- [ ] All internal links work (no 404s)
- [ ] SEO guide pages at /t/{slug} load with ProofBanner, InlineCTA, StickyBottomCTA
- [ ] PostHog captures pageview and cta_click events
- [ ] Google Search Console ownership verified
- [ ] Client added to SEO pages dashboard

---

## Quick Reference: File Structure

```
~/CLIENT-website/
  public/images/           # Logo, headshots, product images
  src/
    app/
      globals.css          # Tailwind 4 theme with brand colors
      layout.tsx           # Root layout: fonts, metadata, JSON-LD ONLY
      sitemap.ts           # All pages with priorities
      robots.ts            # Crawl directives
      (main)/              # Route group: all pages with Header/Footer
        layout.tsx         # Adds Header + Footer around children
        page.tsx           # Homepage
        about/page.tsx
        wins/page.tsx
        how-it-works/page.tsx
        precall/page.tsx
        faq/page.tsx
        blog/page.tsx
        privacy-policy/page.tsx
        t/                 # SEO guide pages
          guide-topic-1/page.tsx
    components/
      Header.tsx
      Footer.tsx
      FAQItem.tsx
      guide-cta-section.tsx
      inline-cta.tsx
      sticky-bottom-cta.tsx
      proof-banner.tsx
```
