# PieLine SEO Guide Page Template

Create a guide page at `src/app/t/{SLUG}/page.tsx` in the pieline-phones repo.

## Page Architecture

PieLine uses GuideNavbar/GuideFooter with PIELINE_THEME. Pages are standalone under `src/app/t/`.

### Imports
```tsx
import type { Metadata } from "next";
import { GuideNavbar, GuideFooter, GuideCTASection, InlineCTA, StickyBottomCTA, ProofBanner } from "@/components/guide";
import { PIELINE_THEME } from "@/components/guide-theme";
import { CTAButton } from "@/components/cta-button";
```

### Page Structure
```tsx
export const metadata: Metadata = {
  title: "{keyword title}",
  description: "{155 chars max}",
  openGraph: { title: "...", description: "...", type: "website" },
  twitter: { card: "summary_large_image", title: "...", description: "..." },
};

export default function Page() {
  return (
    <>
      <GuideNavbar theme={PIELINE_THEME} />
      <article className="max-w-3xl mx-auto px-6 py-16">
        <header className="mb-12">
          <h1 className="text-4xl font-bold tracking-tight mb-4 text-gray-900">
            {Title}
          </h1>
          <p className="text-xl text-gray-600 mb-6">{Lede}</p>
        </header>

        <ProofBanner
          theme={PIELINE_THEME}
          quote="..."
          source="..."
          metric="..."
        />

        <nav className="bg-amber-50 rounded-lg p-6 mb-12">
          <h2 className="text-lg font-semibold mb-3 text-gray-900">Contents</h2>
          <ul className="space-y-2 text-amber-700">...</ul>
        </nav>

        {/* 6-10 content sections */}

        <InlineCTA theme={PIELINE_THEME} />
      </article>
      <GuideCTASection theme={PIELINE_THEME} />
      <GuideFooter theme={PIELINE_THEME} />
      <StickyBottomCTA theme={PIELINE_THEME} />
    </>
  );
}
```

### About PieLine
PieLine is an AI phone agent for restaurants. It answers phone calls, takes orders via voice AI with 95%+ accuracy, integrates directly with POS systems (Square, Toast, Clover), and handles peak call volume without hiring extra staff. Target market: independent restaurants, small chains, QSR franchisees.

### Content Guidelines
- 2,500-4,000 words of expert restaurant operations content
- No em dashes or en dashes anywhere
- Real stats about restaurant industry (missed call rates, phone order revenue, labor costs)
- Practical advice for restaurant owners and operators
- Natural PieLine mentions where relevant (AI phone ordering, POS integration, missed call recovery)
- Include real examples: restaurant types, order scenarios, ROI calculations
- ProofBanner should use real PieLine deployment stats (e.g., Mylapore 11 locations, $500/day additional revenue)

### Tailwind Classes
- Article: `max-w-3xl mx-auto px-6 py-16`
- H1: `text-4xl font-bold tracking-tight mb-4 text-gray-900`
- H2: `text-2xl font-bold mb-4 text-gray-900`
- H3: `text-xl font-semibold mb-3 text-gray-900`
- Body: `text-gray-600 mb-4`
- ToC nav: `bg-amber-50 rounded-lg p-6 mb-12`
- ToC links: `text-amber-700 hover:underline`

### Build and Deploy
```bash
npm run build
vercel --prod --yes
```
