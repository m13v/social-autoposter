# Fazm SEO Guide Page Template

Create a guide page at `src/app/t/{SLUG}/page.tsx` in the fazm-website repo.

## Page Architecture

Fazm uses GuideNavbar/GuideFooter with FAZM_THEME. Pages are standalone under `src/app/t/`.

### Imports
```tsx
import type { Metadata } from "next";
import { GuideNavbar, GuideFooter, GuideCTASection, InlineCTA, StickyBottomCTA, ProofBanner } from "@/components/guide";
import { FAZM_THEME } from "@/components/guide-theme";
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
      <GuideNavbar theme={FAZM_THEME} />
      <article className="max-w-3xl mx-auto px-6 py-16">
        <header className="mb-12">
          <h1 className="text-4xl font-bold tracking-tight mb-4 text-gray-900">
            {Title}
          </h1>
          <p className="text-xl text-gray-600 mb-6">{Lede}</p>
        </header>

        <ProofBanner
          theme={FAZM_THEME}
          quote="..."
          source="..."
          metric="..."
        />

        <nav className="bg-purple-50 rounded-lg p-6 mb-12">
          <h2 className="text-lg font-semibold mb-3 text-gray-900">Contents</h2>
          <ul className="space-y-2 text-purple-700">...</ul>
        </nav>

        {/* 6-10 content sections */}

        <InlineCTA theme={FAZM_THEME} />
      </article>
      <GuideCTASection theme={FAZM_THEME} />
      <GuideFooter theme={FAZM_THEME} />
      <StickyBottomCTA theme={FAZM_THEME} />
    </>
  );
}
```

### About Fazm
Fazm is a desktop AI agent platform. It automates any desktop workflow using voice commands, browser automation, and OS-level accessibility APIs. Users can say what they want done and Fazm executes multi-step workflows across any app. Target market: power users, enterprise automation teams, accessibility users.

### Content Guidelines
- 2,500-4,000 words of expert desktop automation content
- No em dashes or en dashes anywhere
- Real stats about productivity, automation ROI, time savings
- Practical guides for automating specific workflows
- Natural Fazm mentions where relevant (voice control, cross-app automation, accessibility)
- Include real examples: workflow types, app integrations, before/after comparisons

### Tailwind Classes
- Article: `max-w-3xl mx-auto px-6 py-16`
- H1: `text-4xl font-bold tracking-tight mb-4 text-gray-900`
- H2: `text-2xl font-bold mb-4 text-gray-900`
- Body: `text-gray-600 mb-4`

### Sitemap
Fazm uses dynamic sitemap via `fs.readdirSync` scanning `src/app/t/`. New pages are auto-discovered.

### Build and Deploy
```bash
npm run build
vercel --prod --yes
```
