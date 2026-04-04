---
name: client-website
description: "End-to-end workflow for creating, rebuilding, or enhancing a client's website. Covers SEO audit, content extraction, Next.js scaffolding, real image/video assets, structured data, SEO guide pages, component injection, analytics, deployment, and dashboard registration. Use when: 'create client website', 'rebuild website', 'recreate site', 'client landing page', 'SEO pages for client', or when onboarding a new client who needs a web presence."
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
3. For each discovered page, extract:
   - URL and page title
   - All headings (h1 through h6) with hierarchy
   - Full body text (quotes, testimonials, stats, descriptions)
   - CTA text and link targets
   - Form fields (if any)
   - Navigation links (to discover more pages)

**Output:** Complete content inventory organized by page.

### 1c. Extract Visual Assets

Use the isolated browser to catalog images, videos, and embeds:

```js
// In isolated browser, navigate to each page and run:
() => {
  const imgs = Array.from(document.querySelectorAll('img')).map((img, i) => {
    const rect = img.getBoundingClientRect();
    return { idx: i, src: img.src, y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) };
  }).filter(x => x.w > 50);
  const iframes = Array.from(document.querySelectorAll('iframe')).map(f => f.src);
  return { imgs, iframes };
}
```

**Key assets to identify and download:**
- Logo (usually first image, top of page, ~200px wide)
- Hero images or background photos
- Client/team headshot photos (circular, 100-200px)
- Product images (book covers, screenshots, etc.)
- Social proof imagery (awards, certifications, partner logos)
- Video embeds (Vimeo, YouTube URLs)
- Scheduling widgets (Calendly, Cal.com URLs)
- Book cover strips / product galleries

Download all identified images to `public/images/` with descriptive filenames.

### 1d. Take Full Page Screenshots

Capture full-page screenshots of every key page on the original site for visual reference:

```
For each page:
1. browser_navigate to URL
2. browser_take_screenshot with fullPage: true
3. Save as original-{pagename}-full.png
```

---

## Phase 2: Scaffold Project

### 2a. Create Next.js App

```bash
cd ~
npx create-next-app@latest CLIENT-website --typescript --tailwind --eslint --app --src-dir --no-turbopack --import-alias "@/*"
```

### 2b. Configure Theme

Edit `src/app/globals.css` to define the client's brand colors using Tailwind 4 inline theme:

```css
@import "tailwindcss";

:root {
  --background: #ffffff;
  --foreground: #1a1a1a;
  --primary: #CLIENT_COLOR;
  --primary-dark: #CLIENT_COLOR_DARK;
  --accent: #CLIENT_ACCENT;
  /* ... more brand colors */
}

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-primary: var(--primary);
  --color-primary-dark: var(--primary-dark);
  --color-accent: var(--accent);
  --font-sans: var(--font-BODY_FONT);
  --font-heading: var(--font-HEADING_FONT);
}
```

### 2c. Configure Fonts

In `layout.tsx`, import appropriate Google Fonts via `next/font/google`. Match the original site's typography or choose a professional pairing.

---

## Phase 3: Build All Pages

### 3a. Shared Components

Create these reusable components in `src/components/`:

| Component | Purpose |
|-----------|---------|
| `Header.tsx` | Sticky nav with real logo (next/image), dropdown menus, mobile hamburger, CTA button |
| `Footer.tsx` | Multi-column footer with nav links, contact info, social media icons, copyright |

**Header requirements:**
- Use `<Image src="/images/logo.png" ... priority />` for the real logo
- Include dropdown menus for sections with multiple sub-pages (use group-hover or state)
- Mobile: flat list with section headers
- CTA button in nav (e.g., "Book a Call", "Get Started")

**Footer requirements:**
- 3-4 columns: Brand, Company links, Resource links, CTA + contact info
- Social media icon row (SVG icons for each platform)
- Copyright with legal entity name

### 3b. Build Every Page

For each page discovered in Phase 1b, create `src/app/{route}/page.tsx`:

**Every page MUST include:**
- `export const metadata: Metadata` with title, description, OG tags
- JSON-LD BreadcrumbList structured data
- Page-specific JSON-LD (FAQPage for FAQ, Review for testimonials, etc.)
- Real content from the original site (never placeholder text)
- Real images via `<Image>` from next/image
- Proper semantic heading hierarchy (single h1, h2s for sections, h3s for subsections)
- CTA sections linking to the booking/contact page

**Common page types and their requirements:**

| Page | Key Elements |
|------|-------------|
| Homepage | Hero (match original style: light/dark), stats bar, benefits cards, process steps, testimonials with headshots, CTA |
| About | Founder story with photo, team photo, company values, stats, "Who We Work With" |
| Services/How It Works | Process steps, team roles, milestones, testimonials |
| Client Results/Wins | Case studies with headshots, key results with checkmarks, testimonial grid with star ratings |
| Book a Call/Contact | Video embed (Vimeo/YouTube iframe), scheduling widget (Calendly iframe), testimonials sidebar, contact info cards |
| FAQ | Accordion component (client component with useState), JSON-LD FAQPage schema |
| Blog | Post grid with titles, dates, author, excerpts |
| Testimonials | Video placeholder cards with play icon, or quote cards with headshots |
| Free Resources | Card grid linking to sub-pages (guides, trainings, webinars) |
| Privacy Policy | Standard legal text with company entity info |

### 3c. Image Integration Checklist

For every page, verify these images are included:

- [ ] Logo in header and footer (next/image with priority in header)
- [ ] Founder/team headshots where referenced
- [ ] Client headshot photos next to their testimonials (rounded-full)
- [ ] Product images (book covers, screenshots, etc.)
- [ ] Team/office photos in about section
- [ ] Book cover strips or product galleries on homepage
- [ ] All images have descriptive alt text

### 3d. Video and Widget Embeds

Replace any video or scheduling placeholders with real embeds:

```tsx
{/* Vimeo */}
<div className="aspect-video rounded-xl overflow-hidden shadow-lg">
  <iframe
    src="https://player.vimeo.com/video/VIDEO_ID?badge=0&autopause=0"
    width="100%" height="100%" frameBorder="0"
    allow="autoplay; fullscreen; picture-in-picture"
    allowFullScreen title="Video Title" className="w-full h-full"
  />
</div>

{/* Calendly */}
<div className="bg-white rounded-xl shadow-lg overflow-hidden" style={{ minHeight: '700px' }}>
  <iframe
    src="https://calendly.com/USERNAME/MEETING_TYPE?embed_type=Inline&hide_event_type_details=1"
    width="100%" height="700" frameBorder="0"
    title="Schedule a Call" className="w-full"
  />
</div>
```

### 3e. Structured Data

Every page gets JSON-LD. Minimum set:

```tsx
// layout.tsx: Organization (site-wide)
{ "@type": "Organization", "name": "...", "url": "...", "foundingDate": "...", "sameAs": [...] }

// Each page: BreadcrumbList
{ "@type": "BreadcrumbList", "itemListElement": [...] }

// Testimonials/Wins: Review for each case study
{ "@type": "Review", "author": {...}, "reviewBody": "...", "itemReviewed": {...} }

// FAQ: FAQPage
{ "@type": "FAQPage", "mainEntity": [...] }

// Homepage: WebPage
{ "@type": "WebPage", "name": "...", "url": "...", "description": "..." }
```

### 3f. SEO Infrastructure

Create `src/app/sitemap.ts` listing ALL pages with tiered priorities:

| Priority | Pages |
|----------|-------|
| 1.0 | Homepage |
| 0.9 | Core conversion pages (how it works, wins, book a call) |
| 0.8 | Secondary pages (about, faq, contact, blog, podcast) |
| 0.6 | Resource pages (guides, trainings, tools, free resources) |
| 0.3 | Legal pages (privacy policy, terms) |

Create `src/app/robots.ts` with sitemap reference.

---

## Phase 4: Programmatic SEO Guide Pages

### 4a. Create Shared Guide Components

In the client website repo, create these components in `src/components/`:

| Component | File | Purpose |
|-----------|------|---------|
| GuideTheme | `guide-theme.ts` | Brand colors, logo, booking URL, CTA label |
| GuideNavbar | `guide-navbar.tsx` | Sticky nav for guide pages with CTA (tracks `cta_click` in PostHog) |
| GuideFooter | `guide-footer.tsx` | Footer for guide pages |
| GuideCTASection | `guide-cta-section.tsx` | Bottom CTA block with heading, body, subtext |
| InlineCTA | `inline-cta.tsx` | Mid-article CTA break |
| StickyBottomCTA | `sticky-bottom-cta.tsx` | Fixed bottom bar CTA |
| ProofBanner | `proof-banner.tsx` | Social proof quote with metric badge |
| VideoEmbed | `video-embed.tsx` | YouTube/Vimeo embed wrapper |
| CTAButton | `cta-button.tsx` | Styled button with PostHog click tracking |
| guide.ts | `guide.ts` | Re-exports all guide components |

**GuideTheme interface:**

```ts
export interface GuideTheme {
  brand: string;
  logo: string;
  bookingUrl: string;
  bookingLabel?: string;
  colors: {
    primary: string;
    primaryHover: string;
    primaryLight: string;
    primaryText: string;
    gradientFrom: string;
    gradientTo: string;
    accent50: string;
    borderLight: string;
  };
}
```

### 4b. Create SEO Guide Pages

Create guide pages at `src/app/t/{slug}/page.tsx`. Each guide:

**Structure:**
```
GuideNavbar
  article (max-w-3xl)
    header (h1 + intro paragraph)
    ProofBanner (real client metric)
    nav (table of contents with anchor links)
    section#topic-1 (h2 + content + h3 subtopics)
    section#topic-2
    InlineCTA (after 2nd section)
    section#topic-3
    section#topic-4
    GuideCTASection
  StickyBottomCTA
GuideFooter
```

**Content requirements:**
- 2,000+ words of genuinely useful, expert-level content
- Real statistics with sources
- Practical frameworks and actionable advice
- Natural mention of client's service as a solution (not forced)
- Proper heading hierarchy (h1 > h2 > h3)
- Table of contents with anchor links
- Metadata with title, description, OG tags

**Topic selection:**
- Target long-tail keywords in the client's industry
- Focus on problems the client's product/service solves
- Use "how to", "guide", "complete guide", "strategies" patterns
- Each page should have a unique search intent

### 4c. Transform Script

Create a transform script at `~/social-autoposter/scripts/transform_{client}_pages.mjs` that:

1. Iterates through all `/t/[slug]/` directories
2. Injects GuideNavbar, GuideFooter, StickyBottomCTA
3. Adds ProofBanner after `</header>` with real client metrics
4. Adds InlineCTA after the 2nd `</section>`
5. Replaces inline CTA sections with GuideCTASection
6. Skips pages already transformed (checks for `@/components/guide`)

**Template:**

```js
const PROOF_QUOTE = "CLIENT_PROOF_QUOTE";
const PROOF_SOURCE = "CLIENT_NAME, LOCATION";
const PROOF_METRIC = "METRIC_VALUE";
```

---

## Phase 5: Build and Verify

### 5a. Build

```bash
cd ~/CLIENT-website && npm run build
```

Fix any TypeScript or build errors. All routes must compile and generate successfully.

### 5b. Visual Comparison

Use the isolated browser to take full-page screenshots of the new site and compare side-by-side with the originals from Phase 1d.

**Check for:**
- [ ] Real logo (not text placeholder)
- [ ] All client/team photos present
- [ ] Video embeds working (not placeholder cards)
- [ ] Scheduling widget embedded (not placeholder)
- [ ] Book/product images displayed
- [ ] Social media icons in footer
- [ ] Navigation matches original site structure
- [ ] Color scheme matches client brand
- [ ] Mobile responsive (test at 375px width)

---

## Phase 6: Deploy

### 6a. Git and GitHub

```bash
cd ~/CLIENT-website
git init && git add -A
git commit -m "Initial commit: CLIENT_NAME website"
gh repo create m13v/CLIENT-website --private --source=. --push
```

### 6b. Vercel Deployment

```bash
vercel --yes
vercel --prod --yes
```

### 6c. Environment Variables (if using PostHog)

```bash
vercel env add NEXT_PUBLIC_POSTHOG_KEY production <<< "KEY"
vercel env add NEXT_PUBLIC_POSTHOG_HOST production <<< "https://us.i.posthog.com"
```

### 6d. Custom Domain (when ready)

```bash
vercel domains add CLIENT_DOMAIN
```

---

## Phase 7: Register for Tracking

### 7a. Add to SEO Pages Dashboard

Edit `~/social-autoposter-website/src/app/api/dashboard/seo-pages/route.ts` and add the new client to `CLIENT_SEO_CONFIG`:

```ts
clientname: {
  domain: "clientdomain.com",
  baseUrl: "https://clientdomain.com",
  githubRepo: "m13v/CLIENT-website",
},
```

### 7b. Google Search Console

1. Add property in Search Console
2. Add TXT verification record via Vercel DNS
3. Submit sitemap: `https://clientdomain.com/sitemap.xml`

---

## Phase 8: Final Verification Checklist

- [ ] Site loads at production URL
- [ ] All pages render with real content (no placeholder text)
- [ ] All images load (no broken images, no "VIDEO COMING SOON" placeholders)
- [ ] Logo appears in header and footer
- [ ] Navigation dropdowns work on desktop and mobile
- [ ] Video embeds play
- [ ] Scheduling widget loads
- [ ] Social media icons present in footer
- [ ] JSON-LD structured data on every page (validate with Google Rich Results Test)
- [ ] Sitemap accessible at /sitemap.xml
- [ ] robots.txt accessible at /robots.txt
- [ ] Lighthouse desktop score >= 85
- [ ] Lighthouse mobile score >= 70
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
  public/
    images/
      logo.png              # Client logo
      founder.png            # Founder headshot
      team-photo.png         # Team photo
      client-1.png           # Client headshots for testimonials
      client-2.png
      product-1.png          # Product images
      book-covers-strip.png  # Product gallery (if applicable)
  src/
    app/
      globals.css            # Tailwind 4 theme with brand colors
      layout.tsx             # Root layout + Organization JSON-LD
      page.tsx               # Homepage
      sitemap.ts             # All pages with priorities
      robots.ts              # Crawl directives
      about/page.tsx
      wins/page.tsx           # or client-results/
      how-it-works/page.tsx
      precall/page.tsx        # or book-a-call/ or contact/
      faq/page.tsx
      blog/page.tsx
      testimonials/page.tsx
      privacy-policy/page.tsx
      t/
        [slug]/page.tsx       # Dynamic template (optional)
        guide-topic-1/page.tsx
        guide-topic-2/page.tsx
    components/
      Header.tsx
      Footer.tsx
      FAQItem.tsx             # Accordion client component
      guide-theme.ts
      guide-navbar.tsx
      guide-footer.tsx
      guide-cta-section.tsx
      inline-cta.tsx
      sticky-bottom-cta.tsx
      proof-banner.tsx
      cta-button.tsx
      guide.ts                # Re-exports
```
