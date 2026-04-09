# Assrt SEO Guide Page Template

Create a guide page at `src/app/(main)/t/{SLUG}/page.tsx` in the assrt-website repo.

## Page Architecture

Assrt uses a standalone layout (no GuideNavbar/Footer). Pages live inside the `(main)` route group which provides Nav and Footer via layout.tsx.

### Imports
```tsx
import type { Metadata } from "next";
import ProofBanner from "@/components/proof-banner";
import InlineCTA from "@/components/inline-cta";
import GuideCTASection from "@/components/guide-cta-section";
import StickyBottomCTA from "@/components/sticky-bottom-cta";
import RelatedGuides from "@/components/related-guides";
import AnimatedSection from "@/components/animated-section";
import AnimatedCodeBlock from "@/components/animated-code-block";
import CodeComparison from "@/components/code-comparison";
import FlowDiagram from "@/components/flow-diagram";
import SequenceDiagram from "@/components/sequence-diagram";
import TerminalOutput from "@/components/terminal-output";
import MetricsRow from "@/components/metrics-row";
import AnimatedChecklist from "@/components/animated-checklist";
import ScenarioCard from "@/components/scenario-card";
```

### Page Structure
```tsx
export const metadata: Metadata = {
  title: "{keyword}: Guide",
  description: "{155 chars max}",
  alternates: { canonical: "https://assrt.ai/t/{SLUG}" },
  openGraph: { title: "...", description: "...", type: "article", url: "https://assrt.ai/t/{SLUG}" },
  twitter: { card: "summary_large_image", title: "...", description: "..." },
};

export default function Page() {
  return (
    <>
      <article className="mx-auto max-w-3xl px-6 pt-32 pb-20">
        <header className="mb-12">
          <p className="text-xs font-mono uppercase tracking-widest text-emerald-500 mb-3">Category</p>
          <h1 className="text-4xl md:text-5xl font-bold tracking-tighter leading-[1.05] mb-4">
            Title: <span className="text-muted">Subtitle</span>
          </h1>
          <p className="text-lg text-muted leading-relaxed max-w-[55ch]">Lede</p>
          <ProofBanner metric="..." quote="..." source="..." />
        </header>
        <MetricsRow metrics={[...]} />
        <SequenceDiagram title="..." actors={[...]} messages={[...]} />
        <nav className="mb-14 rounded-xl border border-border bg-card p-6">...</nav>
        {/* 8-10 content sections */}
        <InlineCTA />
        <RelatedGuides currentSlug="{SLUG}" />
        <GuideCTASection />
      </article>
      <StickyBottomCTA />
      {/* 3 JSON-LD blocks: TechArticle, BreadcrumbList, FAQPage */}
    </>
  );
}
```

### Rich Media Requirements (minimum per page)
- 1x ProofBanner, 1x MetricsRow (4 metrics), 1x SequenceDiagram
- 1-2x FlowDiagram, 2x+ CodeComparison (Playwright vs Assrt)
- 2x+ TerminalOutput, 3x+ ScenarioCard, 4-6x AnimatedCodeBlock
- 1-2x AnimatedChecklist, 1x RelatedGuides, 1x InlineCTA, 1x GuideCTASection, 1x StickyBottomCTA
- 3 JSON-LD blocks (TechArticle, BreadcrumbList, FAQPage)

### Tailwind Classes
- Article: `mx-auto max-w-3xl px-6 pt-32 pb-20`
- Eyebrow: `text-xs font-mono uppercase tracking-widest text-emerald-500 mb-3`
- H1: `text-4xl md:text-5xl font-bold tracking-tighter leading-[1.05] mb-4`
- H2: `text-2xl md:text-3xl font-bold tracking-tight mb-4`
- Body: `text-muted leading-relaxed mb-4`

### Registration (all three required)
1. `src/app/sitemap.ts` - add slug to `guideSlugs` array
2. `src/app/(main)/site-map/page.tsx` - add to `guides` array
3. `src/components/related-guides.tsx` - add to `GUIDES` record

### Content Rules
- 3,500-4,500 words, expert-level, runnable TypeScript code
- No em dashes or en dashes anywhere
- Real stats, real quotes, real vendor source links
- Every scenario has real Playwright code a reader can paste
- MetricsRow suffix is short units only (%, s, ms, x). Words go in label.

### Build and Deploy
```bash
npm run build  # must succeed
vercel --prod --yes  # manual deploy required
```
