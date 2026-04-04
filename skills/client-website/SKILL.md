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

### 2b. Configure Theme (globals.css)

The theme uses CSS custom properties in `:root` mapped into Tailwind 4 via `@theme inline`. Always define both the CSS variable AND the Tailwind mapping. Every client gets a primary color, a dark variant, and an accent color at minimum.

```css
@import "tailwindcss";

:root {
  --background: #ffffff;
  --foreground: #1a1a1a;
  --primary: #073c61;       /* Main brand color (navy, blue, green, etc.) */
  --primary-dark: #052d49;  /* Darker shade for footer, dark sections */
  --cta: #e11010;            /* CTA button color (red, orange, etc.) */
  --cta-dark: #ae0c0c;      /* CTA hover state */
  --accent: #d4a843;         /* Accent/highlight color (gold, yellow, etc.) */
  --accent-light: #f0d88a;  /* Light accent for badges */
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

body {
  background: var(--background);
  color: var(--foreground);
}

html {
  scroll-behavior: smooth;
}
```

**Color naming convention:** Use semantic names (primary, cta, accent) in the skill templates below. When building for a specific client, you can also add client-specific names (e.g., `--color-navy`, `--color-red`, `--color-gold`) for readability.

### 2c. Configure Fonts (layout.tsx)

Use `next/font/google` with CSS variable mode. The body font goes on `--font-sans`, the heading font on `--font-heading`. Apply both variables to the `<html>` tag.

```tsx
import { Inter, Oswald } from "next/font/google";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const oswald = Oswald({ variable: "--font-oswald", subsets: ["latin"] });

// In <html>:
<html lang="en" className={`${inter.variable} ${oswald.variable} h-full antialiased`}>
  <body className="min-h-full flex flex-col font-sans">
    <Header />
    <main className="flex-1">{children}</main>
    <Footer />
    {/* Organization JSON-LD here */}
  </body>
</html>
```

**Font pairing guide:**
- Professional services: Inter + Oswald (clean, authoritative)
- Creative/lifestyle: Lato + Playfair Display
- Tech/SaaS: DM Sans + Space Grotesk
- Legal/finance: Source Sans 3 + Merriweather

---

## Phase 3: Build All Pages (Design System & Component Blueprints)

This section contains the exact design patterns, Tailwind class combinations, and component structures that produce a polished, professional website. Follow these blueprints precisely for every client site.

### 3a. Header Component Blueprint

Sticky nav with logo, dropdown menus, mobile hamburger, and CTA button.

```tsx
"use client";
import Image from "next/image";
import Link from "next/link";
import { useState } from "react";

// PATTERN: Separate simple links from dropdown groups
const navLinks = [
  { href: "/", label: "Home" },
  { href: "/how-it-works", label: "How It Works" },
  { href: "/wins", label: "Client Results" },
];

const dropdowns = [
  {
    label: "About",
    items: [
      { href: "/about", label: "Our Story" },
      { href: "/faq", label: "FAQ" },
      { href: "/blog", label: "Blog" },
      { href: "/contact", label: "Contact" },
    ],
  },
  // Add more dropdown groups as needed
];

export function Header() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 bg-white border-b border-gray-100 shadow-sm">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-16 items-center justify-between">
          {/* Logo: always use next/image with priority */}
          <Link href="/" className="flex items-center">
            <Image src="/images/logo.png" alt="CLIENT_NAME" width={200} height={66} priority />
          </Link>

          {/* Desktop Nav */}
          <nav className="hidden lg:flex items-center gap-6">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="font-heading text-sm font-semibold uppercase tracking-wider text-gray-700 hover:text-primary transition-colors"
              />
            ))}

            {/* DROPDOWN PATTERN: group + group-hover for CSS-only dropdowns */}
            {dropdowns.map((dropdown) => (
              <div key={dropdown.label} className="relative group">
                <button className="font-heading text-sm font-semibold uppercase tracking-wider text-gray-700 hover:text-primary transition-colors flex items-center gap-1">
                  {dropdown.label}
                  <svg className="h-4 w-4 transition-transform group-hover:rotate-180" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                <div className="invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-all duration-200 absolute top-full left-0 mt-1 w-56 rounded-md bg-white shadow-lg ring-1 ring-black/5 py-2 z-50">
                  {dropdown.items.map((item) => (
                    <Link key={item.href} href={item.href} className="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 hover:text-primary transition-colors" />
                  ))}
                </div>
              </div>
            ))}

            {/* CTA BUTTON in nav */}
            <Link
              href="/precall"
              className="rounded-md bg-cta px-5 py-2.5 font-heading text-sm font-semibold uppercase tracking-wider text-white hover:bg-cta-dark transition-colors"
            />
          </nav>

          {/* MOBILE HAMBURGER */}
          <button className="lg:hidden p-2 text-gray-700" onClick={() => setMobileOpen(!mobileOpen)}>
            <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              {mobileOpen ? (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              )}
            </svg>
          </button>
        </div>

        {/* MOBILE NAV: flat list with section headers */}
        {mobileOpen && (
          <nav className="lg:hidden pb-4 space-y-1">
            {navLinks.map((link) => (
              <Link key={link.href} href={link.href} className="block py-2 font-heading text-sm font-semibold uppercase tracking-wider text-gray-700 hover:text-primary" onClick={() => setMobileOpen(false)} />
            ))}
            {dropdowns.map((dropdown) => (
              <div key={dropdown.label}>
                <span className="block py-2 font-heading text-xs font-bold uppercase tracking-wider text-gray-400 mt-2">{dropdown.label}</span>
                {dropdown.items.map((item) => (
                  <Link key={item.href} href={item.href} className="block py-2 pl-4 font-heading text-sm font-semibold uppercase tracking-wider text-gray-700 hover:text-primary" onClick={() => setMobileOpen(false)} />
                ))}
              </div>
            ))}
            <Link href="/precall" className="block rounded-md bg-cta px-5 py-2.5 text-center font-heading text-sm font-semibold uppercase tracking-wider text-white hover:bg-cta-dark mt-3" onClick={() => setMobileOpen(false)} />
          </nav>
        )}
      </div>
    </header>
  );
}
```

**Key classes:** `sticky top-0 z-50`, `font-heading text-sm font-semibold uppercase tracking-wider`, `invisible opacity-0 group-hover:visible group-hover:opacity-100`

### 3b. Footer Component Blueprint

4-column layout: Brand + description, Company links, Resource links, CTA + contact info. Social icons row at bottom.

```tsx
import Image from "next/image";
import Link from "next/link";

const companyLinks = [
  { href: "/about", label: "About" },
  { href: "/how-it-works", label: "How It Works" },
  { href: "/faq", label: "FAQ" },
  { href: "/contact", label: "Contact" },
  { href: "/blog", label: "Blog" },
  { href: "/privacy-policy", label: "Privacy Policy" },
];

const resourceLinks = [
  { href: "/wins", label: "Client Results" },
  { href: "/testimonials", label: "Testimonials" },
  // ...more resource links
];

export function Footer() {
  return (
    <footer className="bg-primary-dark text-white">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-12">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
          {/* Column 1: Brand */}
          <div>
            <div className="mb-4">
              <Image src="/images/logo.png" alt="CLIENT_NAME" width={200} height={66} />
            </div>
            <p className="text-gray-300 text-sm leading-relaxed">
              Short brand description here.
            </p>
          </div>

          {/* Column 2: Company links */}
          <div>
            <h3 className="font-heading text-sm font-semibold uppercase tracking-wider text-accent mb-4">Company</h3>
            <ul className="space-y-2">
              {companyLinks.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className="text-gray-300 hover:text-white text-sm transition-colors">{link.label}</Link>
                </li>
              ))}
            </ul>
          </div>

          {/* Column 3: Resource links */}
          <div>
            <h3 className="font-heading text-sm font-semibold uppercase tracking-wider text-accent mb-4">Resources</h3>
            <ul className="space-y-2">
              {resourceLinks.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className="text-gray-300 hover:text-white text-sm transition-colors">{link.label}</Link>
                </li>
              ))}
            </ul>
          </div>

          {/* Column 4: CTA + Contact */}
          <div>
            <h3 className="font-heading text-sm font-semibold uppercase tracking-wider text-accent mb-4">Ready to Get Started?</h3>
            <p className="text-gray-300 text-sm mb-4">Short CTA teaser text.</p>
            <Link href="/precall" className="inline-block rounded-md bg-cta px-6 py-3 font-heading text-sm font-semibold uppercase tracking-wider text-white hover:bg-cta-dark transition-colors mb-6" />
            <div className="space-y-1 text-sm text-gray-300">
              <p><a href="mailto:EMAIL" className="hover:text-white transition-colors">EMAIL</a></p>
              <p><a href="tel:+1PHONE" className="hover:text-white transition-colors">PHONE</a></p>
              <p>CITY, STATE</p>
            </div>
          </div>
        </div>

        {/* SOCIAL ICONS ROW */}
        <div className="mt-8 flex justify-center gap-4">
          {/* Each social: circular button with SVG icon */}
          {socialLinks.map((social) => (
            <a key={social.label} href={social.href} aria-label={social.label}
              className="flex h-10 w-10 items-center justify-center rounded-full bg-gray-700 text-white transition-colors hover:bg-accent hover:text-primary">
              {social.icon}
            </a>
          ))}
        </div>

        {/* COPYRIGHT */}
        <div className="mt-8 border-t border-gray-600 pt-8 text-center">
          <p className="text-gray-400 text-sm">&copy; {new Date().getFullYear()} CLIENT_NAME. All rights reserved.</p>
        </div>
      </div>
    </footer>
  );
}
```

**Key classes:** `bg-primary-dark text-white`, `text-accent` for section headers, `h-10 w-10 rounded-full bg-gray-700 hover:bg-accent hover:text-primary` for social icons

### 3c. Section Layout Patterns

These are the exact section patterns used throughout the site. Each section follows a consistent structure.

#### Universal Section Wrapper

```
<section className="bg-{COLOR} py-20 sm:py-28">
  <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
    {/* content */}
  </div>
</section>
```

- Light sections: `bg-white` or `bg-gray-50`
- Dark sections: `bg-primary` with white text
- Section padding: `py-20 sm:py-28` (standard), `py-16 sm:py-24` (compact), `py-12` (stats bar)
- Content max width: `max-w-7xl` for grids, `max-w-3xl` for prose, `max-w-4xl` for CTAs

#### Section Header Pattern

```tsx
<div className="text-center max-w-2xl mx-auto mb-16">
  <h2 className="font-heading text-3xl sm:text-4xl font-bold text-primary mb-4">
    Section Title
  </h2>
  <p className="text-gray-600 text-lg">
    Section subtitle/description text.
  </p>
</div>
```

On dark backgrounds, use `text-white` for h2 and `text-gray-300` for description.

### 3d. Homepage Section Blueprints

The homepage follows this exact section order:

#### 1. Hero Section (White/Light Background)

```tsx
<section className="relative bg-white overflow-hidden">
  <div className="relative mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-24 sm:py-32 lg:py-40">
    <div className="max-w-3xl">
      <p className="font-heading text-primary text-sm sm:text-base font-semibold uppercase tracking-[0.2em] mb-4">
        TAGLINE / CATEGORY
      </p>
      <h1 className="font-heading text-4xl sm:text-5xl lg:text-6xl font-bold text-primary leading-tight mb-6">
        Main Headline <span className="text-accent">(Accent Phrase)</span>
      </h1>
      <p className="text-lg sm:text-xl text-gray-700 leading-relaxed mb-4">
        Primary subheadline.
      </p>
      <p className="text-base sm:text-lg text-gray-500 leading-relaxed mb-10">
        Secondary description with more detail.
      </p>
      <div className="flex flex-col sm:flex-row gap-4">
        {/* PRIMARY CTA */}
        <Link href="/wins" className="inline-flex items-center justify-center rounded-md bg-cta px-8 py-4 font-heading text-base font-semibold uppercase tracking-wider text-white hover:bg-cta-dark transition-colors">
          Primary Action
        </Link>
        {/* SECONDARY CTA (outline) */}
        <Link href="/about#contact" className="inline-flex items-center justify-center rounded-md border-2 border-primary px-8 py-4 font-heading text-base font-semibold uppercase tracking-wider text-primary hover:bg-primary hover:text-white transition-colors">
          Secondary Action
        </Link>
      </div>
    </div>
  </div>
</section>
```

**Key details:**
- Two CTA buttons side by side: solid bg + outline border
- CTA text: `font-heading text-base font-semibold uppercase tracking-wider`
- CTA padding: `px-8 py-4`
- Hero padding is larger than other sections: `py-24 sm:py-32 lg:py-40`
- Accent color used on a key phrase in the headline via `<span className="text-accent">`

#### 2. Product/Image Strip

```tsx
<section className="bg-white py-8 overflow-hidden">
  <div className="mx-auto max-w-7xl px-4">
    <Image src="/images/product-strip.png" alt="Description" width={1116} height={134} className="w-full h-auto" />
  </div>
</section>
```

#### 3. Stats Bar

```tsx
<section className="bg-white border-b border-gray-100">
  <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-12">
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-8">
      {stats.map((stat) => (
        <div key={stat.label} className="text-center">
          <p className="font-heading text-4xl sm:text-5xl font-bold text-primary">{stat.value}</p>
          <p className="mt-2 text-sm font-semibold uppercase tracking-wider text-gray-500">{stat.label}</p>
        </div>
      ))}
    </div>
  </div>
</section>
```

**Data shape:** `{ value: "275+", label: "Books Published" }`

#### 4. Benefits Card Grid (3 columns)

```tsx
<section className="bg-gray-50 py-20 sm:py-28">
  <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
    {/* Section header (centered) */}
    <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
      {benefits.map((benefit) => (
        <div key={benefit.title} className="bg-white rounded-xl p-8 shadow-sm border border-gray-100 hover:shadow-md transition-shadow">
          {/* Icon in colored circle */}
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-lg bg-primary/10 text-primary mb-6">
            {benefit.icon}
          </div>
          <h3 className="font-heading text-xl font-bold text-primary mb-3">{benefit.title}</h3>
          <p className="text-gray-600 leading-relaxed">{benefit.description}</p>
        </div>
      ))}
    </div>
  </div>
</section>
```

**Card pattern:** `bg-white rounded-xl p-8 shadow-sm border border-gray-100 hover:shadow-md transition-shadow`
**Icon container:** `w-14 h-14 rounded-lg bg-primary/10 text-primary`

#### 5. Process/How It Works (4 columns, numbered)

```tsx
<section className="bg-white py-20 sm:py-28">
  <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
    {/* Section header (centered) */}
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
      {steps.map((step) => (
        <div key={step.step} className="relative">
          <div className="text-6xl font-heading font-bold text-primary/10 mb-2">{step.step}</div>
          <h3 className="font-heading text-xl font-bold text-primary mb-3">{step.title}</h3>
          <p className="text-gray-600 leading-relaxed">{step.description}</p>
        </div>
      ))}
    </div>
    {/* Optional team/product photo below */}
    <div className="mt-12">
      <Image src="/images/team-photo.png" alt="Team" width={1024} height={768} className="w-full h-auto rounded-xl shadow-lg" />
    </div>
  </div>
</section>
```

**Step number:** `text-6xl font-heading font-bold text-primary/10` (large, faded background number)

#### 6. Testimonials Section (Dark Background, Glass Cards)

```tsx
<section className="bg-primary py-20 sm:py-28">
  <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
    <div className="text-center max-w-2xl mx-auto mb-16">
      <h2 className="font-heading text-3xl sm:text-4xl font-bold text-white mb-4">Real Results from Real Clients</h2>
      <p className="text-gray-300 text-lg">Description text.</p>
    </div>
    <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
      {testimonials.map((t) => (
        <div key={t.name} className="bg-white/5 border border-white/10 rounded-xl p-8 backdrop-blur-sm">
          {/* Star rating */}
          <div className="flex items-center gap-1 mb-4">
            {[...Array(5)].map((_, i) => (
              <svg key={i} className="h-5 w-5 text-accent" fill="currentColor" viewBox="0 0 20 20">
                <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
              </svg>
            ))}
          </div>
          {/* Quote */}
          <blockquote className="text-white/90 leading-relaxed mb-6 italic">
            &ldquo;{t.quote}&rdquo;
          </blockquote>
          {/* Author with headshot */}
          <div className="border-t border-white/10 pt-4 flex items-start gap-4">
            <Image src={t.image} alt={t.name} width={64} height={64} className="w-16 h-16 rounded-full object-cover flex-shrink-0" />
            <div>
              <p className="font-heading font-bold text-white">{t.name}</p>
              <p className="text-gray-400 text-sm">{t.title}</p>
              <p className="mt-2 text-accent font-heading font-semibold text-sm uppercase tracking-wider">{t.result}</p>
            </div>
          </div>
        </div>
      ))}
    </div>
    {/* CTA button centered below */}
    <div className="text-center mt-12">
      <Link href="/wins" className="inline-flex items-center justify-center rounded-md bg-cta px-8 py-4 font-heading text-base font-semibold uppercase tracking-wider text-white hover:bg-cta-dark transition-colors">
        See All Client Results
      </Link>
    </div>
  </div>
</section>
```

**Glass card:** `bg-white/5 border border-white/10 rounded-xl p-8 backdrop-blur-sm`
**Star SVG path:** `M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z`

#### 7. Final CTA Section (White Background)

```tsx
<section className="bg-white py-20 sm:py-28">
  <div className="mx-auto max-w-4xl px-4 sm:px-6 lg:px-8 text-center">
    <h2 className="font-heading text-3xl sm:text-4xl font-bold text-primary mb-6">
      Ready to Get Started?
    </h2>
    <p className="text-gray-600 text-lg mb-10 max-w-2xl mx-auto">
      Join our clients who have used [PRODUCT] to grow their business.
    </p>
    <div className="flex flex-col sm:flex-row justify-center gap-4">
      <Link href="/precall" className="inline-flex items-center justify-center rounded-md bg-cta px-8 py-4 font-heading text-base font-semibold uppercase tracking-wider text-white hover:bg-cta-dark transition-colors">
        Primary CTA
      </Link>
      <Link href="/wins" className="inline-flex items-center justify-center rounded-md border-2 border-primary px-8 py-4 font-heading text-base font-semibold uppercase tracking-wider text-primary hover:bg-primary hover:text-white transition-colors">
        Secondary CTA
      </Link>
    </div>
  </div>
</section>
```

### 3e. Inner Page Hero Pattern (Dark Background)

Used on About, Wins, FAQ, and all non-homepage pages:

```tsx
<section className="bg-primary py-16 sm:py-24">
  <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 text-center">
    <h1 className="font-heading text-4xl sm:text-5xl lg:text-6xl font-bold text-white mb-6">
      Page Title
    </h1>
    <p className="text-gray-300 text-lg sm:text-xl max-w-2xl mx-auto">
      Page subtitle.
    </p>
  </div>
</section>
```

Variant with gradient overlay (precall, FAQ):

```tsx
<section className="relative bg-primary overflow-hidden">
  <div className="absolute inset-0 bg-gradient-to-br from-primary-dark to-primary opacity-90" />
  <div className="relative mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-24 sm:py-32">
    {/* content with z-index above gradient */}
  </div>
</section>
```

Variant with accent highlight (FAQ):

```tsx
<h1 className="font-heading text-4xl sm:text-5xl lg:text-6xl font-bold text-white leading-tight">
  Frequently Asked <span className="text-accent">Questions</span>
</h1>
```

### 3f. FAQ Accordion Component Blueprint

Client component with useState for expand/collapse:

```tsx
"use client";
import { useState } from "react";

export function FAQItem({ question, answer }: { question: string; answer: string }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="border border-gray-100 rounded-xl overflow-hidden">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between p-6 text-left bg-white hover:bg-gray-50 transition-colors"
      >
        <span className="font-heading text-lg font-bold text-primary pr-4">{question}</span>
        <svg
          className={`h-6 w-6 text-primary shrink-0 transition-transform ${isOpen ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {isOpen && (
        <div className="px-6 pb-6 bg-white">
          <p className="text-gray-600 leading-relaxed">{answer}</p>
        </div>
      )}
    </div>
  );
}
```

**Usage:** Render in a `<div className="space-y-4">` container within a `bg-gray-50` section. Always include JSON-LD FAQPage schema alongside.

### 3g. Case Study / Wins Page Blueprint

Two tiers: featured case studies (detailed cards) and additional testimonials (grid).

#### Featured Case Study Card

```tsx
interface CaseStudy {
  name: string;
  location: string;
  company: string;
  industry: string;
  books: string[];      // or products, services
  quote: string;
  results: string[];
  highlight: string;    // e.g., "30 to 40x ROI"
  headshot: string;
  bookCover?: string;   // optional product image
}

{/* Card with alternating bg */}
<article className={`rounded-2xl border border-gray-100 overflow-hidden shadow-sm ${idx % 2 === 0 ? "bg-gray-50" : "bg-white"}`}>
  <div className="p-8 sm:p-12">
    <div className="flex flex-col lg:flex-row lg:items-start gap-8">
      {/* LEFT COLUMN (2/5): Info */}
      <div className="lg:w-2/5">
        {/* Industry badge */}
        <span className="inline-block bg-primary text-white text-xs font-heading font-semibold uppercase tracking-wider px-3 py-1 rounded-full">
          {cs.industry}
        </span>
        {/* Headshot + name */}
        <div className="flex items-center gap-4 mb-4">
          <Image src={cs.headshot} alt={cs.name} width={80} height={80} className="rounded-full object-cover w-20 h-20" />
          <div>
            <h3 className="font-heading text-2xl sm:text-3xl font-bold text-primary mb-1">{cs.name}</h3>
            <p className="text-gray-500 mb-0.5">{cs.company}</p>
            <p className="text-gray-400 text-sm">{cs.location}</p>
          </div>
        </div>
        {/* Book/product tags */}
        <div className="flex flex-wrap gap-2">
          {cs.books.map((book) => (
            <span key={book} className="inline-block bg-accent/20 text-primary text-sm font-medium px-3 py-1 rounded-md italic">{book}</span>
          ))}
        </div>
        {/* Highlight metric box */}
        <div className="bg-primary rounded-lg p-4 text-center">
          <p className="text-accent font-heading font-bold text-xl">{cs.highlight}</p>
        </div>
      </div>

      {/* RIGHT COLUMN (3/5): Quote + Results */}
      <div className="lg:w-3/5">
        <blockquote className="text-lg sm:text-xl text-gray-700 italic leading-relaxed mb-8 border-l-4 border-accent pl-6">
          &ldquo;{cs.quote}&rdquo;
        </blockquote>
        <p className="text-sm font-semibold uppercase tracking-wider text-gray-500 mb-3">Key Results</p>
        <ul className="space-y-2">
          {cs.results.map((result) => (
            <li key={result} className="flex items-start gap-3">
              <svg className="h-5 w-5 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span className="text-gray-700">{result}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  </div>
</article>
```

#### Additional Testimonials Grid

```tsx
<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
  {testimonials.map((t) => (
    <div key={t.name} className="bg-white rounded-xl p-6 shadow-sm border border-gray-100 hover:shadow-md transition-shadow flex flex-col">
      {/* Star rating */}
      <div className="flex items-center gap-1 mb-3">
        {[...Array(5)].map((_, i) => (
          <svg key={i} className="h-4 w-4 text-accent" fill="currentColor" viewBox="0 0 20 20">
            <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l..." />
          </svg>
        ))}
      </div>
      <blockquote className="text-gray-700 italic leading-relaxed mb-4 flex-1">
        &ldquo;{t.quote}&rdquo;
      </blockquote>
      <div className="border-t border-gray-100 pt-3">
        <p className="font-heading font-bold text-primary text-sm">{t.name}</p>
        <p className="text-gray-500 text-xs">{t.company}</p>
        {t.result && (
          <p className="mt-1 text-accent font-heading font-semibold text-xs uppercase tracking-wider">{t.result}</p>
        )}
      </div>
    </div>
  ))}
</div>
```

### 3h. Book a Call / Precall Page Blueprint

Two-column layout: left (2/3) has video + scheduling widget, right (1/3) has testimonials sidebar.

```tsx
{/* Hero with founder headshot + gradient */}
<section className="relative bg-primary overflow-hidden">
  <div className="absolute inset-0 bg-gradient-to-br from-primary-dark to-primary opacity-90" />
  <div className="relative mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-24 sm:py-32">
    <div className="max-w-3xl mx-auto text-center">
      <Image src="/images/founder.png" alt="Founder Name" width={200} height={200} className="rounded-full mx-auto mb-6" />
      <h1 className="font-heading text-4xl sm:text-5xl lg:text-6xl font-bold text-white leading-tight mb-6">
        Ready to Get Started? <span className="text-accent">Book a Call Today!</span>
      </h1>
    </div>
  </div>
</section>

{/* Main Content: 2-column */}
<section className="bg-white py-20 sm:py-28">
  <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-12">
      {/* LEFT (2/3): Video + Calendly */}
      <div className="lg:col-span-2 space-y-16">
        {/* Video */}
        <div>
          <h2 className="font-heading text-2xl sm:text-3xl font-bold text-primary mb-6">Watch This Video</h2>
          <div className="aspect-video rounded-xl overflow-hidden shadow-lg">
            <iframe src="https://player.vimeo.com/video/VIDEO_ID?badge=0&autopause=0" width="100%" height="100%" frameBorder="0" allow="autoplay; fullscreen; picture-in-picture" allowFullScreen className="w-full h-full" />
          </div>
        </div>
        {/* Scheduling */}
        <div>
          <h2 className="font-heading text-2xl sm:text-3xl font-bold text-primary mb-6">Select a Time Below</h2>
          <div className="bg-white rounded-xl shadow-lg overflow-hidden" style={{ minHeight: '700px' }}>
            <iframe src="https://calendly.com/USER/MEETING?embed_type=Inline&hide_event_type_details=1" width="100%" height="700" frameBorder="0" className="w-full" />
          </div>
        </div>
      </div>

      {/* RIGHT (1/3): Testimonial Sidebar */}
      <div className="space-y-6">
        <h3 className="font-heading text-xl font-bold text-primary mb-4">What Our Clients Say</h3>
        {testimonials.map((t) => (
          <div key={t.name} className="bg-gray-50 rounded-xl p-6 border border-gray-100">
            {/* Stars */}
            <blockquote className="text-gray-700 leading-relaxed mb-3 italic text-sm">&ldquo;{t.quote}&rdquo;</blockquote>
            <div className="flex items-center gap-3">
              {t.headshot && <Image src={t.headshot} alt={t.name} width={48} height={48} className="w-12 h-12 rounded-full object-cover" />}
              <p className="font-heading font-bold text-primary text-sm">{t.name}</p>
            </div>
          </div>
        ))}
        {/* Contact Info Card */}
        <div className="bg-primary rounded-xl p-6 mt-8">
          <h3 className="font-heading text-lg font-bold text-white mb-4">Get in Touch</h3>
          <div className="space-y-3">
            <a href="mailto:EMAIL" className="flex items-center gap-3 text-gray-300 hover:text-white transition-colors text-sm">
              {/* Email SVG icon in text-accent */} EMAIL
            </a>
            <a href="tel:+1PHONE" className="flex items-center gap-3 text-gray-300 hover:text-white transition-colors text-sm">
              {/* Phone SVG icon in text-accent */} PHONE
            </a>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>
```

### 3i. About Page Blueprint

Section order: Dark Hero, Stats Bar, Founder Story (prose with photo), Team Photo, Values Grid (2-col), "Who We Serve" Checklist Grid (3-col), Contact CTA (dark bg with glass card).

**Values card grid:**
```tsx
<div className="grid grid-cols-1 md:grid-cols-2 gap-8">
  {values.map((v) => (
    <div key={v.title} className="bg-white rounded-xl p-8 shadow-sm border border-gray-100">
      <h3 className="font-heading text-xl font-bold text-primary mb-3">{v.title}</h3>
      <p className="text-gray-600 leading-relaxed">{v.description}</p>
    </div>
  ))}
</div>
```

**"Who We Serve" checklist grid:**
```tsx
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 max-w-4xl mx-auto">
  {professions.map((profession) => (
    <div key={profession} className="flex items-center gap-3 bg-gray-50 rounded-lg p-4">
      <svg className="h-5 w-5 text-accent shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
      <span className="text-primary font-medium">{profession}</span>
    </div>
  ))}
</div>
```

**Contact CTA glass card (dark section):**
```tsx
<div className="bg-white/10 border border-white/20 rounded-2xl p-8 sm:p-12 max-w-xl mx-auto">
  <h3 className="font-heading text-2xl font-bold text-white mb-6">Card Title</h3>
  <div className="space-y-4 text-left mb-8">
    {items.map((item) => (
      <div key={item} className="flex items-start gap-3">
        <svg className="h-5 w-5 text-accent mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
        <span className="text-gray-300">{item}</span>
      </div>
    ))}
  </div>
  <Link href="/precall" className="inline-flex items-center justify-center w-full rounded-md bg-cta px-8 py-4 font-heading text-base font-semibold uppercase tracking-wider text-white hover:bg-cta-dark transition-colors">
    CTA Text
  </Link>
</div>
```

### 3j. Reusable SVG Icons

**Checkmark (for results, features, checklists):**
```tsx
<svg className="h-5 w-5 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
</svg>
```

**Chevron down (for dropdowns, accordions):**
```tsx
<svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
</svg>
```

**Star (for ratings):**
```tsx
<svg className="h-5 w-5 text-accent" fill="currentColor" viewBox="0 0 20 20">
  <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
</svg>
```

**Hamburger / Close (for mobile nav):**
```tsx
{/* Hamburger */}
<path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
{/* Close X */}
<path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
```

### 3k. Typography Quick Reference

| Element | Classes |
|---------|---------|
| Page h1 | `font-heading text-4xl sm:text-5xl lg:text-6xl font-bold text-primary leading-tight` |
| Section h2 | `font-heading text-3xl sm:text-4xl font-bold text-primary mb-4` |
| Card h3 | `font-heading text-xl font-bold text-primary mb-3` |
| Body text | `text-gray-600 leading-relaxed` |
| Tagline/label | `font-heading text-sm font-semibold uppercase tracking-wider text-gray-500` |
| Accent label | `font-heading text-sm font-semibold uppercase tracking-[0.2em]` |
| Stat number | `font-heading text-4xl sm:text-5xl font-bold text-primary` |
| Stat label | `text-sm font-semibold uppercase tracking-wider text-gray-500` |
| Quote text | `text-gray-700 italic leading-relaxed` |
| Dark bg h2 | `font-heading text-3xl sm:text-4xl font-bold text-white mb-4` |
| Dark bg body | `text-gray-300 text-lg` |
| CTA button | `font-heading text-base font-semibold uppercase tracking-wider` |
| Nav link | `font-heading text-sm font-semibold uppercase tracking-wider text-gray-700` |

### 3l. Color Usage Quick Reference

| Context | Light section | Dark section |
|---------|--------------|-------------|
| Background | `bg-white` or `bg-gray-50` | `bg-primary` or `bg-primary-dark` |
| Heading | `text-primary` | `text-white` |
| Body text | `text-gray-600` | `text-gray-300` |
| Subtle text | `text-gray-500` | `text-gray-400` |
| Card | `bg-white rounded-xl p-8 shadow-sm border border-gray-100` | `bg-white/5 border border-white/10 rounded-xl p-8` |
| CTA button | `bg-cta text-white hover:bg-cta-dark` | Same |
| Outline button | `border-2 border-primary text-primary hover:bg-primary hover:text-white` | `border-2 border-white text-white hover:bg-white hover:text-primary` |
| Accent highlight | `text-accent` | `text-accent` |
| Badge | `bg-primary text-white text-xs px-3 py-1 rounded-full` | N/A |
| Divider | `border-gray-100` | `border-white/10` |

### 3m. Image Integration Checklist

For every page, verify these images are included:

- [ ] Logo in header (with `priority`) and footer (both via next/image)
- [ ] Founder/team headshots where referenced
- [ ] Client headshot photos next to testimonials (`rounded-full object-cover`)
- [ ] Product images (book covers, screenshots, etc.)
- [ ] Team/office photos in about section (`rounded-xl shadow-lg`)
- [ ] Product gallery or strip on homepage (`w-full h-auto`)
- [ ] All images have descriptive alt text

### 3n. Video and Widget Embeds

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

### 3o. Structured Data

Every page gets JSON-LD. Minimum set:

```tsx
// layout.tsx: Organization (site-wide)
{ "@type": "Organization", "name": "...", "url": "...", "foundingDate": "...", "numberOfEmployees": { "@type": "QuantitativeValue", "value": N }, "sameAs": [...] }

// Each page: BreadcrumbList
{ "@type": "BreadcrumbList", "itemListElement": [{ "@type": "ListItem", "position": 1, "name": "Home", "item": "https://domain.com" }, ...] }

// Wins page: Review for each case study
{ "@type": "Review", "author": { "@type": "Person", "name": "..." }, "reviewBody": "...", "itemReviewed": { "@type": "Service", "name": "...", "provider": { "@type": "Organization", "name": "..." } }, "reviewRating": { "@type": "Rating", "ratingValue": 5, "bestRating": 5 } }

// FAQ page: FAQPage
{ "@type": "FAQPage", "mainEntity": [{ "@type": "Question", "name": "...", "acceptedAnswer": { "@type": "Answer", "text": "..." } }] }

// Homepage: WebPage
{ "@type": "WebPage", "name": "...", "url": "...", "description": "..." }
```

### 3p. SEO Infrastructure

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
