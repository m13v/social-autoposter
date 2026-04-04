#!/usr/bin/env node
// Transform all Fazm SEO guide pages to use reusable components
import { readFileSync, writeFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

const PAGES_DIR = join(process.env.HOME, "fazm-website/src/app/t");

const NEW_IMPORTS = `import type { Metadata } from "next";
import { GuideNavbar, GuideFooter, GuideCTASection, InlineCTA, StickyBottomCTA, ProofBanner } from "@/components/guide";
import { FAZM_THEME } from "@/components/guide-theme";
import { CTAButton } from "@/components/cta-button";`;

function transformPage(filePath) {
  let content = readFileSync(filePath, "utf-8");
  const original = content;

  // Skip if already transformed
  if (content.includes("@/components/guide")) return false;

  // 1. Replace imports
  content = content.replace(
    /import type \{ Metadata \} from "next";\s*\nimport \{ CTAButton \} from "@\/components\/cta-button";/,
    NEW_IMPORTS
  );

  // 2. Wrap return with fragment + GuideNavbar
  content = content.replace(
    "return (\n    <article",
    "return (\n    <>\n      <GuideNavbar theme={FAZM_THEME} />\n      <article"
  );

  // 3. Add ProofBanner after </header>
  content = content.replace(
    "</header>",
    `</header>

        <ProofBanner
          theme={FAZM_THEME}
          quote="Fazm uses real accessibility APIs instead of screenshots, so it interacts with any app on your Mac reliably and fast. Free to start, fully open source."
          source="fazm.ai"
          metric="OSS"
        />`
  );

  // 4. Add InlineCTA after 2nd </section>
  let sectionCount = 0;
  content = content.replace(/<\/section>/g, (m) => {
    sectionCount++;
    if (sectionCount === 2) {
      return `</section>

          <InlineCTA
            theme={FAZM_THEME}
            heading="Try the AI agent that actually works with your apps"
            body="Fazm uses accessibility APIs to control your Mac natively. Voice-first, open source, runs locally."
          />`;
    }
    return m;
  });

  // 5. Replace bottom CTA gradient section with GuideCTASection
  const ctaRegex = /\s*<section className="bg-gradient-to-r from-teal-600 to-teal-800 rounded-2xl[^"]*"[^>]*>\s*<h2[^>]*>([\s\S]*?)<\/h2>\s*<p[^>]*>\s*([\s\S]*?)\s*<\/p>\s*<CTAButton[^>]*>\s*([\s\S]*?)\s*<\/CTAButton>\s*<\/section>/;
  const ctaMatch = content.match(ctaRegex);
  if (ctaMatch) {
    const heading = ctaMatch[1].replace(/\s+/g, " ").trim();
    const body = ctaMatch[2].replace(/\s+/g, " ").trim();
    content = content.replace(ctaRegex, `
        <GuideCTASection
          theme={FAZM_THEME}
          heading="${escapeJsx(heading)}"
          body="${escapeJsx(body)}"
          subtext="Free to start. Fully open source. Runs locally on your Mac."
        />`);
  }

  // 6. Replace inline footer + close article with GuideFooter + StickyBottomCTA + close fragment
  const footerPattern = /\s*<footer className="mt-16[\s\S]*?<\/footer>\s*\n\s*<\/article>\s*\n\s*\);\s*\n\s*\}/;
  if (footerPattern.test(content)) {
    content = content.replace(footerPattern, `
      </article>
      <StickyBottomCTA theme={FAZM_THEME} text="AI computer agent for macOS, free and open source" />
      <GuideFooter theme={FAZM_THEME} />
    </>
  );
}`);
  } else {
    // No inline footer, just wrap closing
    content = content.replace(
      /\s*<\/article>\s*\n\s*\);\s*\n\s*\}/,
      `
      </article>
      <StickyBottomCTA theme={FAZM_THEME} text="AI computer agent for macOS, free and open source" />
      <GuideFooter theme={FAZM_THEME} />
    </>
  );
}`
    );
  }

  if (content === original) return false;
  writeFileSync(filePath, content);
  return true;
}

function escapeJsx(str) {
  return str
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;")
    .replace(/{/g, "&#123;")
    .replace(/}/g, "&#125;");
}

const dirs = readdirSync(PAGES_DIR).filter((d) => {
  const full = join(PAGES_DIR, d);
  return statSync(full).isDirectory() && d !== "[slug]";
});

let transformed = 0;
let skipped = 0;
for (const dir of dirs) {
  const pagePath = join(PAGES_DIR, dir, "page.tsx");
  try {
    if (transformPage(pagePath)) {
      console.log(`OK: ${dir}`);
      transformed++;
    } else {
      console.log(`SKIP: ${dir}`);
      skipped++;
    }
  } catch (e) {
    console.error(`ERROR: ${dir}: ${e.message}`);
  }
}
console.log(`\nDone: ${transformed} transformed, ${skipped} skipped out of ${dirs.length} total`);
