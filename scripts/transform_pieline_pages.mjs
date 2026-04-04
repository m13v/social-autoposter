#!/usr/bin/env node
// Transform all PieLine SEO guide pages to use reusable components
import { readFileSync, writeFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

const PAGES_DIR = join(process.env.HOME, "pieline-phones/src/app/t");
const PROOF_QUOTE = "Mylapore (11 locations): projecting $500 additional revenue per location per day from eliminating phone bottleneck. 90%+ of calls handled end-to-end by AI.";
const PROOF_SOURCE = "Mylapore, Bay Area (11 locations)";
const PROOF_METRIC = "$500/day";

const NEW_IMPORTS = `import type { Metadata } from "next";
import { GuideNavbar, GuideFooter, GuideCTASection, InlineCTA, StickyBottomCTA, ProofBanner } from "@/components/guide";
import { PIELINE_THEME } from "@/components/guide-theme";
import { CTAButton } from "@/components/cta-button";`;

function transformPage(filePath) {
  let content = readFileSync(filePath, "utf-8");
  const original = content;

  // 1. Replace imports
  content = content.replace(
    /import type \{ Metadata \} from "next";\s*\nimport \{ CTAButton \} from "@\/components\/cta-button";/,
    NEW_IMPORTS
  );

  // 2. Wrap in fragment with GuideNavbar/GuideFooter if not already wrapped
  // PieLine pages use: <article className="max-w-3xl mx-auto px-6 py-16">
  // Need to wrap in <>  <GuideNavbar /> <article>...</article> <GuideFooter /> </>
  if (!content.includes("GuideNavbar")) {
    // Add GuideNavbar before <article>
    content = content.replace(
      /return \(\s*\n\s*<article/,
      `return (
    <>
      <GuideNavbar theme={PIELINE_THEME} />
      <article`
    );

    // Replace the inline footer + closing with GuideFooter
    // PieLine pages have a footer section then close </article>
    const footerRegex = /\s*<footer className="mt-16 pt-8 border-t[^"]*"[\s\S]*?<\/footer>\s*\n\s*<\/article>/;
    if (footerRegex.test(content)) {
      content = content.replace(
        footerRegex,
        `
      </article>
      <StickyBottomCTA theme={PIELINE_THEME} text="AI phone answering from $350/mo, free 7-day trial" />
      <GuideFooter theme={PIELINE_THEME} />`
      );
    } else {
      // No inline footer, just close article and add components
      content = content.replace(
        /\s*<\/article>\s*\n\s*\);/,
        `
      </article>
      <StickyBottomCTA theme={PIELINE_THEME} text="AI phone answering from $350/mo, free 7-day trial" />
      <GuideFooter theme={PIELINE_THEME} />
    </>
  );`
      );
    }

    // Close the fragment if we opened one
    if (content.includes("<>") && !content.includes("</>")) {
      content = content.replace(/\s*\);?\s*$/, "\n    </>\n  );\n}\n");
    }
  }

  // 3. Replace bottom CTA section with GuideCTASection
  // PieLine pattern: <div className="bg-gradient-to-r from-amber-500 to-orange-600...
  const ctaRegex = /\s*<div className="bg-gradient-to-r from-amber-500 to-orange-600 rounded-2xl[^"]*"[^>]*>\s*<h2[^>]*>([\s\S]*?)<\/h2>\s*<p[^>]*>\s*([\s\S]*?)\s*<\/p>\s*<CTAButton[^>]*>\s*([\s\S]*?)\s*<\/CTAButton>\s*<\/div>/;
  const ctaMatch = content.match(ctaRegex);
  if (ctaMatch) {
    const heading = ctaMatch[1].replace(/\s+/g, " ").trim();
    const body = ctaMatch[2].replace(/\s+/g, " ").trim();
    const replacement = `
        <GuideCTASection
          theme={PIELINE_THEME}
          heading="${escapeJsx(heading)}"
          body="${escapeJsx(body)}"
          subtext="Free 7-day trial. No contracts. Works with any POS."
        />`;
    content = content.replace(ctaRegex, replacement);
  }

  // 4. Add ProofBanner after </header>
  if (!content.includes("ProofBanner")) {
    content = content.replace(
      /(<\/header>)/,
      `$1

        <ProofBanner
          theme={PIELINE_THEME}
          quote="${escapeJsx(PROOF_QUOTE)}"
          source="${PROOF_SOURCE}"
          metric="${PROOF_METRIC}"
        />`
    );
  }

  // 5. Add InlineCTA after second </section>
  if (!content.includes("InlineCTA")) {
    let sectionCount = 0;
    content = content.replace(/<\/section>/g, (match) => {
      sectionCount++;
      if (sectionCount === 2) {
        return `</section>

          <InlineCTA
            theme={PIELINE_THEME}
            heading="Stop losing revenue to missed calls"
            body="PieLine answers every call 24/7, takes orders with 95%+ accuracy, and sends them straight to your POS."
          />`;
      }
      return match;
    });
  }

  // 6. Ensure the fragment is properly closed
  // Fix cases where </> might be missing
  const openFragments = (content.match(/<>/g) || []).length;
  const closeFragments = (content.match(/<\/>/g) || []).length;
  if (openFragments > closeFragments) {
    // Add closing fragment before the final );
    content = content.replace(/(\s*\);?\s*}\s*)$/, "\n    </>\n  );\n}\n");
  }

  if (content === original) {
    return false;
  }
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

// Main
const dirs = readdirSync(PAGES_DIR).filter((d) => {
  const full = join(PAGES_DIR, d);
  return statSync(full).isDirectory() && d !== "[slug]";
});

let transformed = 0;
let skipped = 0;

for (const dir of dirs) {
  const pagePath = join(PAGES_DIR, dir, "page.tsx");
  try {
    const content = readFileSync(pagePath, "utf-8");
    if (content.includes("@/components/guide")) {
      console.log(`SKIP (already transformed): ${dir}`);
      skipped++;
      continue;
    }
    const ok = transformPage(pagePath);
    if (ok) {
      console.log(`OK: ${dir}`);
      transformed++;
    } else {
      console.log(`SKIP (no changes): ${dir}`);
      skipped++;
    }
  } catch (e) {
    console.error(`ERROR: ${dir}: ${e.message}`);
  }
}

console.log(`\nDone: ${transformed} transformed, ${skipped} skipped out of ${dirs.length} total`);
