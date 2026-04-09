# Cyrano SEO Guide Page Template

Create a guide page at `src/app/t/{SLUG}/page.tsx` in the cyrano-security repo.

## Page Architecture

Cyrano uses GuideNavbar/GuideFooter with CYRANO_THEME. Pages are standalone under `src/app/t/`.

### Imports
```tsx
import type { Metadata } from "next";
import { GuideNavbar, GuideFooter, GuideCTASection, InlineCTA, StickyBottomCTA, ProofBanner, VideoEmbed } from "@/components/guide";
import { CYRANO_THEME } from "@/components/guide-theme";
import { CTAButton } from "@/components/cta-button";
```

### Page Structure
```tsx
export const metadata: Metadata = {
  title: "{keyword title}",
  description: "{155 chars max}",
  openGraph: { title: "...", description: "...", type: "article" },
  twitter: { card: "summary_large_image", title: "...", description: "..." },
  robots: "index, follow",
};

export default function Page() {
  return (
    <>
      <GuideNavbar theme={CYRANO_THEME} />
      <article className="max-w-3xl mx-auto px-6 py-16 md:py-24">
        <header className="mb-12">
          <div className="inline-flex items-center gap-2 bg-blue-100 text-blue-700 text-sm font-medium px-3 py-1.5 rounded-full mb-6">
            Security Guide
          </div>
          <h1 className="text-3xl md:text-4xl lg:text-5xl font-bold text-gray-900 leading-tight">
            {Title}
          </h1>
          <p className="mt-6 text-lg text-gray-600 leading-relaxed">{Lede}</p>
        </header>

        <ProofBanner
          theme={CYRANO_THEME}
          quote="..."
          source="..."
          metric="..."
        />

        <nav className="bg-gray-50 rounded-xl p-6 mb-12">
          <p className="text-sm font-semibold text-gray-900 mb-3">In this guide:</p>
          <ul className="space-y-2 text-sm text-blue-600">...</ul>
        </nav>

        <div className="prose prose-lg max-w-none">
          {/* 6-10 content sections with h2/h3 */}
        </div>

        <InlineCTA theme={CYRANO_THEME} />
      </article>
      <GuideCTASection theme={CYRANO_THEME} />
      <GuideFooter theme={CYRANO_THEME} />
      <StickyBottomCTA theme={CYRANO_THEME} />
    </>
  );
}
```

### About Cyrano
Cyrano is an AI-powered physical security monitoring platform. It connects to existing security cameras (RTSP/ONVIF) and uses computer vision to detect incidents (break-ins, vandalism, loitering, package theft) in real-time, sending instant alerts. Target market: multifamily property managers, commercial real estate, retail.

### Content Guidelines
- 2,500-4,000 words of expert security industry content
- No em dashes or en dashes anywhere
- Real stats about security industry (property crime rates, camera failure rates, response times)
- Practical, actionable advice for property managers and security directors
- Natural Cyrano mentions where relevant (AI monitoring, camera health checks, incident detection)
- Include real examples: property types, incident scenarios, ROI calculations
- ProofBanner should use real Cyrano deployment stats or industry data

### Tailwind Classes
- Article: `max-w-3xl mx-auto px-6 py-16 md:py-24`
- Badge: `inline-flex items-center gap-2 bg-blue-100 text-blue-700 text-sm font-medium px-3 py-1.5 rounded-full mb-6`
- H1: `text-3xl md:text-4xl lg:text-5xl font-bold text-gray-900 leading-tight`
- H2: `text-2xl font-bold text-gray-900 mb-4`
- Body: `text-gray-600 mb-4`
- ToC nav: `bg-gray-50 rounded-xl p-6 mb-12`
- ToC links: `text-blue-600 hover:underline`

### Build and Deploy
```bash
npm run build
vercel --prod --yes
```
