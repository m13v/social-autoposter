# Assrt SEO Guide Page Template

Create a guide page at `src/app/(main)/t/{SLUG}/page.tsx` in the assrt-website repo.

## Goal

Each page should feel like it was written for its specific topic, not stamped from a mold. Vary the structure, the visual signature, and the component mix based on what the topic actually needs. Avoid the "kitchen sink" pattern where every page imports every component.

**Before you start**: list 3 existing pages in `src/app/(main)/t/` and read 2 of them. Pick a shape *different* from those two. If you keep recreating the same layout, you are doing this wrong.

## Page Architecture

Assrt pages live inside the `(main)` route group which provides Nav and Footer via `layout.tsx`. You write a single `page.tsx` exporting `metadata` and a default component.

## Step 1: Pick an archetype

Look at the keyword and choose ONE archetype. Do not blend them. Each has its own visual signature, structure, length range, and component menu.

### Archetype A: Deep Technical Guide
**Use when**: keyword is broad ("ai testing", "test automation"), reader needs architecture + theory + practice.
- **Length**: 3,000–4,500 words
- **Eyebrow color**: `text-emerald-500`
- **Max width**: `max-w-3xl`
- **Hero**: H1 + lede + one strong proof signal (ProofBanner OR a single MetricsRow, not both)
- **Open with**: short TL;DR paragraph, then table of contents
- **Core**: 6–10 H2 sections, mix of prose, code, and one architecture diagram
- **Component picks (use 4–6 total, NOT all)**: SequenceDiagram or FlowDiagram (one, not both), 2–3 AnimatedCodeBlock, 1 CodeComparison, 1 ScenarioCard cluster, optional TerminalOutput

### Archetype B: Comparison / Alternatives
**Use when**: keyword is "X vs Y", "best X alternative", "X tools".
- **Length**: 1,800–2,800 words
- **Eyebrow color**: `text-sky-500`
- **Max width**: `max-w-4xl` (wider for tables)
- **Hero**: H1 + lede, NO ProofBanner, NO MetricsRow
- **Open with**: a real comparison table immediately after the lede (this IS the value)
- **Core**: per-tool sections (3–6 tools), each with strengths, weaknesses, code snippet, pricing reality
- **Component picks (use 3–5 total)**: 1 `<Comparison />` or raw table, 2–3 CodeComparison, optional 1 ScenarioCard. **No SequenceDiagram, no FlowDiagram, no MetricsRow.**

### Archetype C: Quickstart Tutorial
**Use when**: keyword starts with "how to", "tutorial", "guide to setting up", or names a single concrete task.
- **Length**: 1,200–2,200 words
- **Eyebrow color**: `text-amber-500`
- **Max width**: `max-w-2xl` (narrower, reads like a recipe)
- **Hero**: H1 + one-sentence lede ("In 6 minutes you will...")
- **Open with**: prerequisites checklist, then numbered steps
- **Core**: 5–8 numbered steps. Each step = short prose + one code block + (optionally) terminal output
- **Component picks (use 3–4 total)**: AnimatedChecklist for prereqs, 4–6 AnimatedCodeBlock, 1–2 TerminalOutput. **No ProofBanner, no MetricsRow, no diagrams.**

### Archetype D: Concept Explainer
**Use when**: keyword is a definition or "what is X" question.
- **Length**: 1,500–2,500 words
- **Eyebrow color**: `text-violet-500`
- **Max width**: `max-w-3xl`
- **Hero**: H1 + 2-sentence lede that answers the question literally in the first 50 words (AI Overview bait)
- **Open with**: a 50-word definition box, then a single labeled diagram
- **Core**: 4–6 H2 sections building from the definition outward (history, mechanism, examples, anti-patterns)
- **Component picks (use 3–4 total)**: 1 FlowDiagram OR SequenceDiagram (whichever fits), 1–2 AnimatedCodeBlock, 1 ScenarioCard. **No ProofBanner, no MetricsRow.**

## Step 2: Component menu (optional, not mandatory)

Available components in `@/components/`:

| Component | Use for | Don't use when |
|---|---|---|
| `ProofBanner` | one strong stat + quote + source | the topic has no real stat to anchor |
| `MetricsRow` | 4 short metrics that tell a story together | you only have 1–2 metrics; use ProofBanner instead |
| `SequenceDiagram` | actor-to-actor message flow over time | the topic isn't about an interaction |
| `FlowDiagram` | step-by-step pipeline or branching logic | linear prose would explain it faster |
| `CodeComparison` | side-by-side "Playwright way vs Assrt way" | only one approach is relevant |
| `AnimatedCodeBlock` | a runnable snippet you want to highlight | you have 3+ snippets in a row — collapse to plain `<pre>` |
| `TerminalOutput` | showing real CLI output | the output is fake/placeholder |
| `ScenarioCard` | 3+ parallel scenarios with structured fields | scenarios are paragraphs of prose |
| `AnimatedChecklist` | prerequisites or before/after lists | the items are full sentences |
| `Comparison` | tool/option comparison table | a markdown table is enough |
| `InlineCTA` | mid-article CTA after a strong moment | every page has one already |
| `GuideCTASection` | end-of-article CTA block | optional, omit if InlineCTA suffices |
| `StickyBottomCTA` | persistent mobile CTA | always include this one |
| `RelatedGuides` | end-of-article internal links | optional, include if 4+ related slugs exist |

**Hard rules**:
- **Never include a component "because the template says so".** If it doesn't serve the specific topic, leave it out.
- **Never bundle the full kitchen sink.** If you're importing more than 7 components from `@/components/`, stop and cut.
- The only mandatory components are: `StickyBottomCTA` (always) and ONE of {`InlineCTA`, `GuideCTASection`} (your choice).

## Step 3: Page skeleton

```tsx
import type { Metadata } from "next";
// import ONLY the components your archetype needs
import StickyBottomCTA from "@/components/sticky-bottom-cta";
// ...other imports based on the archetype menu

export const metadata: Metadata = {
  title: "<write a real headline, not '{keyword}: Guide'>",
  description: "<155 chars, click-worthy, contains the keyword naturally>",
  alternates: { canonical: "https://assrt.ai/t/{SLUG}" },
  openGraph: {
    title: "<can differ from <title>>",
    description: "<can differ>",
    type: "article",
    url: "https://assrt.ai/t/{SLUG}",
  },
  twitter: { card: "summary_large_image", title: "...", description: "..." },
};

export default function Page() {
  return (
    <>
      <article className="mx-auto {ARCHETYPE_MAX_WIDTH} px-6 pt-32 pb-20">
        <header className="mb-12">
          <p className="text-xs font-mono uppercase tracking-widest {ARCHETYPE_EYEBROW_COLOR} mb-3">
            {/* Real category, NOT "Table of Contents" */}
          </p>
          <h1 className="text-4xl md:text-5xl font-bold tracking-tighter leading-[1.05] mb-4">
            {/* Real headline. Subtitle in <span className="text-muted"> is optional, not required. */}
          </h1>
          <p className="text-lg text-muted leading-relaxed max-w-[55ch]">
            {/* Lede */}
          </p>
        </header>

        {/* Body — varies by archetype. See archetype rules above. */}

        {/* End-of-article CTA (one of InlineCTA or GuideCTASection) */}
      </article>
      <StickyBottomCTA />
      {/* JSON-LD: pick what's relevant. TechArticle + BreadcrumbList are the safe pair.
          Add FAQPage ONLY if the page has 3+ genuine Q&A pairs that match real reader questions. */}
    </>
  );
}
```

## Title rules (relax these, but keep them)

- **Do not** use the literal pattern `"{Keyword}: Guide"`. Write a real headline.
- **Do not** use the eyebrow text `"Table of Contents"`. Use a real category like `"Test Automation"`, `"Tooling"`, `"Architecture"`.
- The H1 may include a `<span className="text-muted">` subtitle, or not. Vary it.

## Tailwind class reference

Use these exact classes when you do use them. Don't invent new ones.

| Element | Class |
|---|---|
| Article wrapper | `mx-auto {max-w-2xl|max-w-3xl|max-w-4xl} px-6 pt-32 pb-20` (pick by archetype) |
| Eyebrow | `text-xs font-mono uppercase tracking-widest {text-emerald-500|text-sky-500|text-amber-500|text-violet-500} mb-3` (pick by archetype) |
| H1 | `text-4xl md:text-5xl font-bold tracking-tighter leading-[1.05] mb-4` |
| H2 | `text-2xl md:text-3xl font-bold tracking-tight mb-4` |
| Body paragraph | `text-muted leading-relaxed mb-4` |

## Registration (all three required, regardless of archetype)

1. `src/app/sitemap.ts` — add slug to `guideSlugs` array
2. `src/app/(main)/site-map/page.tsx` — add to `guides` array
3. `src/components/related-guides.tsx` — add to `GUIDES` record

## Content rules

- **Word count**: see archetype range. Do not pad to hit a minimum. A 1,400-word quickstart that gets to the point is better than a 4,000-word filler version.
- **No em dashes or en dashes anywhere.**
- **No AI vocabulary**: delve, crucial, robust, comprehensive, nuanced, multifaceted, furthermore, moreover, additionally, pivotal, landscape, tapestry, underscore, foster, showcase, intricate, vibrant, fundamental.
- **Real stats, real quotes, real vendor links.** No fabricated numbers.
- **Runnable TypeScript code only.** No pseudocode.
- **MetricsRow suffix is short units only** (`%`, `s`, `ms`, `x`). Words go in label.
- **Footer tie-in** is one sentence at the end of content. Don't hard-sell earlier.

## Anti-patterns (the things that make pages feel mass-produced)

- Importing every component "just in case". Cut anything you don't actively use in the body.
- Using `text-emerald-500` on every page. Rotate by archetype.
- Starting every page with ProofBanner + MetricsRow. Half the archetypes don't use either.
- Title pattern `"<Keyword>: <subtitle>"`. Write real headlines.
- Eyebrow text `"Table of Contents"`. Use a real category.
- Forcing 3,500+ words on a topic that fits in 1,500. The reader can tell.
- Including SequenceDiagram on a comparison page, or a comparison table on a quickstart. Wrong tool for the topic.

## Build and Deploy

```bash
npm run build  # must succeed
vercel --prod --yes  # manual deploy required
```

## Self-check before shipping

- [ ] Did I pick a single archetype and stick to it?
- [ ] Did I read 2 existing pages and pick a *different* shape?
- [ ] Did I use the archetype's eyebrow color and max-width (not the default emerald + max-w-3xl)?
- [ ] Are all my imports actively used in the JSX? (Cut unused.)
- [ ] Is the title a real headline, not `"{keyword}: Guide"`?
- [ ] Is the eyebrow a real category, not `"Table of Contents"`?
- [ ] Does every component on the page genuinely serve this specific topic?
- [ ] Is the word count inside the archetype range, not padded to 3,500+?
