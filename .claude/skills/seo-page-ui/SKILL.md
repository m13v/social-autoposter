---
name: seo-page-ui
description: "UI blueprint for building rich SEO landing pages with Next.js/TSX. Defines the exact section order, animated SVG patterns, comparison tables, FAQ accordions, CTA blocks, JSON-LD structured data, and color palette. Use when building any /use-case/, /t/, or landing page that needs to rank in search. Project-agnostic: reads project config from social-autoposter config.json."
user_invocable: true
---

# SEO Page UI

A reusable UI blueprint for building rich, SEO-optimized landing pages in Next.js (TSX). This skill defines the visual component patterns, section order, animated SVG templates, comparison tables, and structured data that every SEO landing page should have.

This is the **UI layer only**. It does not handle keyword discovery, state files, or pipeline orchestration. It tells you *what to build* visually.

## When to Use

- Building a `/use-case/`, `/t/`, or `/guide/` page targeting a keyword
- Creating any SEO landing page that needs rich media
- When a pipeline skill (like `gsc-seo-page` or `underserved-seo-page`) needs to know the page structure
- Referenced automatically by `gsc-seo-page` when creating pages

## Step 0: Resolve Project Context

Before building, determine the project from context (the calling skill, the repo you're in, or the user's instruction). Read `~/social-autoposter/config.json` to get:

| Variable | Source | Fallback |
|---|---|---|
| `PROJECT_NAME` | `projects[].name` | Ask user |
| `DOMAIN` | `projects[].website` | Ask user |
| `BASE_URL` | `projects[].landing_pages.base_url` or `projects[].website` | Ask user |
| `BOOKING_LINK` | `projects[].booking_link` | `/contact` |
| `CTA_TEXT` | Derive from project type | "Get Started" |
| `CTA_HREF` | `/download` for apps, booking link for services | Derive |

### Project Color Palette

Read the project's CSS/Tailwind config to extract the actual colors. If unavailable, use these defaults:

| Token | Hex | Usage |
|---|---|---|
| Box fill | `#1e293b` | SVG rectangles, diagram backgrounds |
| Dark background | `#0f172a` | Optional deeper background fills |
| Primary stroke | `#14b8a6` | Borders, lines, non-agent boxes |
| Agent highlight | `#2dd4bf` | Product box, animated dots, bold text |
| Dark accent | `#0d9488` | Arrow labels, connection lines |
| Text primary | `#e2e8f0` | Headings, labels inside SVG |
| Text secondary | `#94a3b8` | Sub-labels, captions |
| Warning | `#f59e0b` | Warning callouts |
| Error | `#ef4444` | Error callouts |

If the project has a light theme, invert appropriately (dark text on light backgrounds).

CSS classes for the page body use the site's design tokens. Common patterns: `text-white`, `text-muted`, `text-accent`, `bg-surface-light/50`, `border-white/5`, `border-accent/20`. Adapt to whatever tokens the project already uses.

---

## File Structure

Each page is a single TSX file. Determine the route from the project's existing content structure:

```tsx
import { Metadata } from "next";
import Link from "next/link";
// Import the project's Navbar/Header and Footer components
// (names vary per project: Navbar, Header, Nav, etc.)

export const metadata: Metadata = { /* ... */ };

export default function PageName() {
  const jsonLd = [ /* ... */ ];

  return (
    <main>
      {/* Project's nav component */}
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <div className="max-w-4xl mx-auto px-6 py-24">
        {/* All 11 sections here */}
      </div>
      {/* Project's footer component */}
    </main>
  );
}
```

---

## Required Metadata

```tsx
export const metadata: Metadata = {
  title: "{Topic} - {Subtitle} | {PROJECT_NAME}",
  description: "155-160 char meta description with primary keyword naturally included.",
  keywords: [
    "primary keyword",
    "variation 1",
    "variation 2",
    "variation 3",
  ],
  alternates: { canonical: "{BASE_URL}/{route}/{slug}" },
  openGraph: {
    title: "{Topic} - {PROJECT_NAME}",
    description: "Short OG description under 200 chars.",
    type: "website",
    url: "{BASE_URL}/{route}/{slug}",
    siteName: "{PROJECT_NAME}",
  },
  twitter: {
    card: "summary_large_image",
    title: "{Topic} - {PROJECT_NAME}",
    description: "Short Twitter description.",
  },
};
```

---

## Required JSON-LD (4 blocks)

Every page must include these 4 structured data blocks in a `jsonLd` array:

### 1. WebPage

```tsx
{
  "@context": "https://schema.org",
  "@type": "WebPage",
  name: "{Topic}",
  description: "Same as meta description.",
  url: "{BASE_URL}/{route}/{slug}",
}
```

### 2. BreadcrumbList

```tsx
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  itemListElement: [
    { "@type": "ListItem", position: 1, name: "Home", item: "{BASE_URL}" },
    { "@type": "ListItem", position: 2, name: "{Section}", item: "{BASE_URL}/{route}" },
    { "@type": "ListItem", position: 3, name: "{Topic}", item: "{BASE_URL}/{route}/{slug}" },
  ],
}
```

### 3. HowTo

4 steps showing how to use the product for this use case:

```tsx
{
  "@context": "https://schema.org",
  "@type": "HowTo",
  name: "How to {achieve outcome} with {PROJECT_NAME}",
  description: "Step-by-step guide.",
  step: [
    { "@type": "HowToStep", position: 1, name: "Step 1 title", text: "Step 1 detail." },
    { "@type": "HowToStep", position: 2, name: "Step 2 title", text: "Step 2 detail." },
    { "@type": "HowToStep", position: 3, name: "Step 3 title", text: "Step 3 detail." },
    { "@type": "HowToStep", position: 4, name: "Step 4 title", text: "Step 4 detail." },
  ],
}
```

### 4. FAQPage

4 Q&A pairs. First FAQ differentiates from competitors. Must match FAQ section content exactly.

```tsx
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: [
    {
      "@type": "Question",
      name: "How is {PROJECT_NAME} different from {competitor}?",
      acceptedAnswer: { "@type": "Answer", text: "..." },
    },
    // ... 3 more
  ],
}
```

---

## The 11 Required Sections

Every SEO page must have all 11 sections in this exact order.

### Section 1: Breadcrumbs

```tsx
<nav aria-label="Breadcrumb" className="mb-8">
  <ol className="flex items-center gap-2 text-sm text-muted">
    <li><Link href="/" className="hover:text-white transition-colors">Home</Link></li>
    <li>/</li>
    <li><Link href="/{route}" className="hover:text-white transition-colors">{Section}</Link></li>
    <li>/</li>
    <li className="text-white">{Topic}</li>
  </ol>
</nav>
```

### Section 2: H1 + Lede

```tsx
<h1 className="text-4xl sm:text-5xl font-bold text-white mb-6">
  {Topic Title}
</h1>
<p className="text-lg text-muted max-w-3xl mb-8">
  {2-4 sentences. Lead with a concrete stat or pain point. Explain why existing
  solutions fall short. Position the product as the alternative. No filler.}
</p>
```

### Section 3: Hero Animated SVG

A flow diagram showing data moving through the product to target apps/outcomes. Must include SMIL `<animate>` elements for moving dots.

**Layout:** Source (left) -> Product (center) -> Target outcomes (right, stacked 2-3)

```tsx
<div className="my-8 max-w-lg mx-auto">
  <svg viewBox="0 0 400 120" className="w-full" xmlns="http://www.w3.org/2000/svg">
    {/* Source box */}
    <rect x="10" y="35" width="90" height="50" rx="10"
      fill="#1e293b" stroke="#14b8a6" strokeWidth="2" />
    <text x="55" y="58" textAnchor="middle" fill="#e2e8f0"
      fontSize="11" fontFamily="sans-serif">{Source}</text>
    <text x="55" y="73" textAnchor="middle" fill="#94a3b8"
      fontSize="9" fontFamily="sans-serif">{Subtitle}</text>

    {/* Product box - slightly larger, brighter stroke */}
    <rect x="155" y="30" width="90" height="60" rx="10"
      fill="#1e293b" stroke="#2dd4bf" strokeWidth="2.5" />
    <text x="200" y="57" textAnchor="middle" fill="#2dd4bf"
      fontSize="12" fontWeight="bold" fontFamily="sans-serif">{PROJECT_NAME}</text>
    <text x="200" y="73" textAnchor="middle" fill="#94a3b8"
      fontSize="9" fontFamily="sans-serif">{Product subtitle}</text>

    {/* Target boxes - 3 stacked on right */}
    <rect x="300" y="10" width="90" height="30" rx="6"
      fill="#1e293b" stroke="#14b8a6" strokeWidth="1.5" />
    <text x="345" y="30" textAnchor="middle" fill="#e2e8f0"
      fontSize="10" fontFamily="sans-serif">{Outcome1}</text>

    <rect x="300" y="48" width="90" height="30" rx="6"
      fill="#1e293b" stroke="#14b8a6" strokeWidth="1.5" />
    <text x="345" y="68" textAnchor="middle" fill="#e2e8f0"
      fontSize="10" fontFamily="sans-serif">{Outcome2}</text>

    <rect x="300" y="86" width="90" height="30" rx="6"
      fill="#1e293b" stroke="#14b8a6" strokeWidth="1.5" />
    <text x="345" y="106" textAnchor="middle" fill="#e2e8f0"
      fontSize="10" fontFamily="sans-serif">{Outcome3}</text>

    {/* Dashed connection lines */}
    <line x1="100" y1="60" x2="155" y2="60"
      stroke="#0d9488" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.5" />
    <line x1="245" y1="50" x2="300" y2="25"
      stroke="#0d9488" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.5" />
    <line x1="245" y1="60" x2="300" y2="63"
      stroke="#0d9488" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.5" />
    <line x1="245" y1="70" x2="300" y2="101"
      stroke="#0d9488" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.5" />

    {/* Animated dots - 2 dots with staggered timing */}
    <circle r="4" fill="#2dd4bf">
      <animate attributeName="cx" values="100;155;245;300" dur="2.5s" repeatCount="indefinite" />
      <animate attributeName="cy" values="60;60;50;25" dur="2.5s" repeatCount="indefinite" />
      <animate attributeName="opacity" values="1;0.8;0.6;1" dur="2.5s" repeatCount="indefinite" />
    </circle>
    <circle r="4" fill="#14b8a6">
      <animate attributeName="cx" values="100;155;245;300" dur="2.5s" repeatCount="indefinite" begin="0.8s" />
      <animate attributeName="cy" values="60;60;60;63" dur="2.5s" repeatCount="indefinite" begin="0.8s" />
      <animate attributeName="opacity" values="1;0.8;0.6;1" dur="2.5s" repeatCount="indefinite" begin="0.8s" />
    </circle>
  </svg>
</div>
```

**Customization per page:** Change source label, product subtitle, and target names. Adjust `cy` values so each dot flows to a different target box.

### Section 4: Problem + Comparison Table

`<section className="mb-16">`

H2 naming the pain point. 2 paragraphs: first explains the manual pain, second explains why competing tools miss the angle.

Then the comparison table:

```tsx
<div className="overflow-x-auto rounded-xl border border-white/5">
  <table className="w-full text-sm text-left">
    <thead>
      <tr className="border-b border-white/10 bg-surface-light/50">
        <th className="px-4 py-3 text-muted font-medium"></th>
        <th className="px-4 py-3 text-muted font-medium">Manual</th>
        <th className="px-4 py-3 text-muted font-medium">Competitors ({names})</th>
        <th className="px-4 py-3 text-accent font-semibold">{PROJECT_NAME}</th>
      </tr>
    </thead>
    <tbody className="divide-y divide-white/5">
      {/* Alternate rows: odd rows get bg-surface-light/30 */}
      <tr className="bg-surface-light/30">
        <td className="px-4 py-3 text-white font-medium">{Dimension}</td>
        <td className="px-4 py-3 text-muted">{Manual}</td>
        <td className="px-4 py-3 text-muted">{Competitor}</td>
        <td className="px-4 py-3 text-white">{Product}</td>
      </tr>
      {/* 5-6 rows total */}
    </tbody>
  </table>
</div>
```

**Standard dimensions:** Setup time, core capability, integrations, pricing/value, data privacy, support.

### Section 5: Example Prompts / Use Cases (7 items)

```tsx
<section className="mb-16">
  <h2 className="text-2xl font-bold text-white mb-6">
    What You Can Do with {PROJECT_NAME}
  </h2>
  <div className="space-y-3">
    {[
      "First use case with specific details...",
      "Second use case...",
      // ... 7 total
    ].map((prompt) => (
      <div key={prompt} className="p-4 rounded-xl bg-surface-light/50 border border-white/5">
        <div className="flex items-start gap-3">
          <span className="text-accent mt-0.5">&#10003;</span>
          <span className="text-white text-sm">&quot;{prompt}&quot;</span>
        </div>
      </div>
    ))}
  </div>
</section>
```

Each item must name real tools, apps, or scenarios. Not generic.

### Section 6: Workflow Diagram + Steps

A wider workflow SVG (viewBox `0 0 720 120`) with arrowhead markers, then 4 numbered steps.

```tsx
<section className="mb-16">
  <h2 className="text-2xl font-bold text-white mb-6">
    How {PROJECT_NAME} Works
  </h2>

  <div className="my-8">
    <svg viewBox="0 0 720 120" className="w-full" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="arrowhead-{unique-id}" markerWidth="10" markerHeight="7"
          refX="10" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#2dd4bf" />
        </marker>
      </defs>

      {/* Source box */}
      <rect x="20" y="30" width="160" height="60" rx="10"
        fill="#1e293b" stroke="#14b8a6" strokeWidth="2" />
      <text x="100" y="56" textAnchor="middle" fill="#e2e8f0"
        fontSize="14" fontWeight="bold" fontFamily="sans-serif">Input</text>
      <text x="100" y="74" textAnchor="middle" fill="#94a3b8"
        fontSize="11" fontFamily="sans-serif">{source details}</text>

      {/* Arrow with label */}
      <line x1="180" y1="60" x2="268" y2="60"
        stroke="#2dd4bf" strokeWidth="2" markerEnd="url(#arrowhead-{unique-id})" />
      <text x="224" y="48" textAnchor="middle" fill="#0d9488"
        fontSize="10" fontFamily="sans-serif">{action verb}</text>

      {/* Product box */}
      <rect x="280" y="25" width="160" height="70" rx="10"
        fill="#1e293b" stroke="#2dd4bf" strokeWidth="2.5" />
      <text x="360" y="55" textAnchor="middle" fill="#2dd4bf"
        fontSize="15" fontWeight="bold" fontFamily="sans-serif">{PROJECT_NAME}</text>
      <text x="360" y="75" textAnchor="middle" fill="#94a3b8"
        fontSize="11" fontFamily="sans-serif">{product subtitle}</text>

      {/* Arrow with label */}
      <line x1="440" y1="60" x2="528" y2="60"
        stroke="#2dd4bf" strokeWidth="2" markerEnd="url(#arrowhead-{unique-id})" />
      <text x="484" y="48" textAnchor="middle" fill="#0d9488"
        fontSize="10" fontFamily="sans-serif">{action verb}</text>

      {/* Target box */}
      <rect x="540" y="30" width="160" height="60" rx="10"
        fill="#1e293b" stroke="#14b8a6" strokeWidth="2" />
      <text x="620" y="56" textAnchor="middle" fill="#e2e8f0"
        fontSize="14" fontWeight="bold" fontFamily="sans-serif">Result</text>
      <text x="620" y="74" textAnchor="middle" fill="#94a3b8"
        fontSize="11" fontFamily="sans-serif">{outcome details}</text>
    </svg>
  </div>

  {/* 4 numbered steps */}
  <div className="space-y-6">
    {[
      { step: "1", title: "Step 1 title", desc: "..." },
      { step: "2", title: "Step 2 title", desc: "..." },
      { step: "3", title: "Step 3 title", desc: "..." },
      { step: "4", title: "Step 4 title", desc: "..." },
    ].map((item) => (
      <div key={item.step} className="flex gap-4">
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-accent/20 flex items-center justify-center text-accent font-bold text-sm">
          {item.step}
        </div>
        <div>
          <h3 className="text-white font-semibold mb-1">{item.title}</h3>
          <p className="text-muted text-sm">{item.desc}</p>
        </div>
      </div>
    ))}
  </div>
</section>
```

**Important:** Use a unique marker ID per page (e.g., `arrowhead-cs` for security cameras, `arrowhead-po` for phone ordering) to avoid SVG ID collisions.

### Section 7: Benefits (3 Cards)

```tsx
<section className="mb-16">
  <h2 className="text-2xl font-bold text-white mb-6">
    Why {PROJECT_NAME} Over {Competitor Category}
  </h2>
  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
    {[
      { title: "Benefit 1 headline", desc: "..." },
      { title: "Benefit 2 headline", desc: "..." },
      { title: "Benefit 3 headline", desc: "..." },
    ].map((item) => (
      <div key={item.title} className="p-5 rounded-xl bg-surface-light/50 border border-white/5">
        <h3 className="text-white font-semibold mb-2">{item.title}</h3>
        <p className="text-muted text-sm">{item.desc}</p>
      </div>
    ))}
  </div>
</section>
```

**No icons** on benefit cards. Text only.

### Section 8: Real-World Scenario

```tsx
<section className="mb-16">
  <h2 className="text-2xl font-bold text-white mb-6">A Real-World Example</h2>
  <div className="p-5 rounded-xl bg-surface-light/50 border border-white/5">
    <p className="text-muted text-sm mb-3">
      {Setup: who the person is, their role, the problem they face,
      with concrete numbers (e.g., "120 units", "8-12 incidents per month")}
    </p>
    <p className="text-white text-sm font-medium mb-3">
      &quot;{How they used the product, 2-4 sentences, specific}&quot;
    </p>
    <p className="text-muted text-sm">
      {The measurable result with before/after comparison.
      Always include concrete numbers.}
    </p>
  </div>
</section>
```

### Section 9: FAQ Accordions (4 items)

```tsx
<section className="mb-16">
  <h2 className="text-2xl font-bold text-white mb-6">Frequently Asked Questions</h2>
  <div className="space-y-4">
    {[
      {
        q: "How is {PROJECT_NAME} different from {competitor}?",
        a: "...",
      },
      {
        q: "Can {PROJECT_NAME} handle {specific capability}?",
        a: "...",
      },
      {
        q: "How does {specific feature} work?",
        a: "...",
      },
      {
        q: "Is my data safe with {PROJECT_NAME}?",
        a: "...",
      },
    ].map((faq) => (
      <details key={faq.q} className="p-5 rounded-xl bg-surface-light/50 border border-white/5 group">
        <summary className="text-white font-semibold cursor-pointer list-none flex items-center justify-between">
          {faq.q}
          <span className="text-muted group-open:rotate-45 transition-transform text-xl">+</span>
        </summary>
        <p className="text-muted text-sm mt-3">{faq.a}</p>
      </details>
    ))}
  </div>
</section>
```

**FAQ content must match the FAQPage JSON-LD exactly** (same questions, same answers).

### Section 10: CTA

```tsx
<section className="mb-16 text-center p-8 rounded-2xl bg-gradient-to-b from-accent/10 to-transparent border border-accent/20">
  <h2 className="text-2xl font-bold text-white mb-3">
    {Action-oriented headline}
  </h2>
  <p className="text-muted mb-6">
    {One sentence reinforcing the value prop}
  </p>
  <Link
    href="{CTA_HREF}"
    className="inline-flex items-center gap-2 px-6 py-3 rounded-full bg-accent text-white font-semibold hover:bg-accent/90 transition-colors"
  >
    {CTA_TEXT}
  </Link>
</section>
```

For products with downloads: CTA_HREF = `/download`, CTA_TEXT = "Download {PROJECT_NAME}"
For services with bookings: CTA_HREF = booking link from config, CTA_TEXT = "Book a Demo"

### Section 11: Related Links (6 items)

```tsx
<section className="pt-8 border-t border-white/5">
  <h2 className="text-xl font-bold text-white mb-4">Related Pages</h2>
  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
    {[
      { href: "/{route}/{slug1}", label: "{Name}", desc: "{One line}" },
      { href: "/{route}/{slug2}", label: "{Name}", desc: "{One line}" },
      { href: "/{route}/{slug3}", label: "{Name}", desc: "{One line}" },
      { href: "/{route}/{slug4}", label: "{Name}", desc: "{One line}" },
      { href: "/{route}/{slug5}", label: "{Name}", desc: "{One line}" },
      { href: "{CTA_HREF}", label: "{CTA_TEXT}", desc: "{Short desc}" },
    ].map((link) => (
      <Link key={link.href} href={link.href}
        className="p-4 rounded-xl bg-surface-light/50 border border-white/5 hover:border-accent/20 transition-all">
        <div className="text-white font-semibold text-sm">{link.label}</div>
        <div className="text-xs text-muted mt-1">{link.desc}</div>
      </Link>
    ))}
  </div>
</section>
```

Last link is always the CTA. Pick 5 topically related pages from existing content.

---

## Writing Rules

- **No em dashes or en dashes.** Use commas, semicolons, colons, parentheses, or new sentences.
- **No AI vocabulary:** delve, crucial, robust, comprehensive, nuanced, multifaceted, furthermore, moreover, additionally, pivotal, landscape, tapestry, underscore, foster, showcase, intricate, vibrant, fundamental.
- **Use the project's accent colors.** Read the site's CSS to match. Defaults are teal.
- **No decorative icons** on cards. The checkmark in example prompts (`&#10003;`) is the only icon.
- **Concrete examples** with real names, real numbers, real workflows.
- **Second person** ("you") or **first person plural** ("we") voice.
- **Minimum 1,200 words** of visible text.

## Quality Checklist

- [ ] All 11 sections present in correct order
- [ ] Hero animated SVG with SMIL `<animate>` dots
- [ ] Workflow SVG with arrowhead `<marker>` and unique marker ID
- [ ] Comparison table: Manual vs Competitors vs Product (5-6 rows)
- [ ] 7 example prompts/use cases with real specifics
- [ ] 3 benefit cards, no icons
- [ ] Real-world scenario with before/after numbers
- [ ] 4 FAQ accordions matching FAQPage JSON-LD word-for-word
- [ ] CTA with correct href (download or booking link)
- [ ] 6 related links (5 content pages + CTA)
- [ ] 4 JSON-LD blocks (WebPage, BreadcrumbList, HowTo, FAQPage)
- [ ] Metadata: title, description, keywords, canonical, OG, Twitter
- [ ] Project colors used, no em dashes, no AI vocabulary
- [ ] No broken image/video references
