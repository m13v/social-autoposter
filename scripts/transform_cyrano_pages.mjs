#!/usr/bin/env node
// Transform all Cyrano SEO guide pages to use reusable components
import { readFileSync, writeFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

const PAGES_DIR = join(process.env.HOME, "cyrano-security/src/app/t");
const PROOF_QUOTE = "At one Class C multifamily property in Fort Worth, Cyrano caught 20 incidents including a break-in attempt in the first month. Customer renewed after 30 days.";
const PROOF_SOURCE = "Fort Worth, TX property deployment";
const PROOF_METRIC = "20";
const VIDEO_URL = "https://youtu.be/_xYiJbH6S_A";

const NEW_IMPORTS = `import type { Metadata } from "next";
import { GuideNavbar, GuideFooter, GuideCTASection, InlineCTA, StickyBottomCTA, ProofBanner, VideoEmbed } from "@/components/guide";
import { CYRANO_THEME } from "@/components/guide-theme";
import { CTAButton } from "@/components/cta-button";`;

function transformPage(filePath) {
  let content = readFileSync(filePath, "utf-8");
  const original = content;

  // 1. Replace imports
  content = content.replace(
    /import type \{ Metadata \} from "next";\s*\nimport \{ CTAButton \} from "@\/components\/cta-button";/,
    NEW_IMPORTS
  );

  // 2. Remove BOOKING_URL const
  content = content.replace(/\nconst BOOKING_URL = "[^"]*";\n/, "\n");

  // 3. Remove inline Navbar function (multi-line)
  content = content.replace(/\nfunction Navbar\(\) \{[\s\S]*?^}\n/m, "\n");

  // 4. Remove inline Footer function (multi-line)
  content = content.replace(/\nfunction Footer\(\) \{[\s\S]*?^}\n/m, "\n");

  // 5. Replace <Navbar /> with <GuideNavbar>
  content = content.replace(
    /\s*<Navbar \/>/g,
    "\n      <GuideNavbar theme={CYRANO_THEME} />"
  );

  // 6. Replace <Footer /> with <GuideFooter>
  content = content.replace(
    /\s*<Footer \/>/g,
    "\n      <GuideFooter theme={CYRANO_THEME} />"
  );

  // 7. Replace bottom CTA section with GuideCTASection
  // Match the gradient CTA div and extract heading, body, button text, subtext
  const ctaRegex = /\s*<div className="bg-gradient-to-br from-blue-500 to-blue-700 rounded-2xl[\s\S]*?<h2[^>]*>\s*([\s\S]*?)\s*<\/h2>\s*<p className="text-blue-100[^"]*"[^>]*>\s*([\s\S]*?)\s*<\/p>\s*<CTAButton[^>]*>\s*([\s\S]*?)\s*<\/CTAButton>(?:\s*<p className="text-xs text-blue-200[^"]*"[^>]*>\s*([\s\S]*?)\s*<\/p>)?\s*<\/div>/;
  const ctaMatch = content.match(ctaRegex);
  if (ctaMatch) {
    const heading = ctaMatch[1].replace(/\s+/g, " ").trim();
    const body = ctaMatch[2].replace(/\s+/g, " ").trim();
    const subtext = ctaMatch[4] ? ctaMatch[4].replace(/\s+/g, " ").trim() : null;
    const replacement = `
        <GuideCTASection
          theme={CYRANO_THEME}
          heading="${escapeJsx(heading)}"
          body="${escapeJsx(body)}"${subtext ? `\n          subtext="${escapeJsx(subtext)}"` : ""}
        />`;
    content = content.replace(ctaRegex, replacement);
  }

  // 8. Add ProofBanner + VideoEmbed after the </header> tag
  content = content.replace(
    /(<\/header>)/,
    `$1

        <ProofBanner
          theme={CYRANO_THEME}
          quote="${escapeJsx(PROOF_QUOTE)}"
          source="${PROOF_SOURCE}"
          metric="${PROOF_METRIC}"
        />

        <VideoEmbed videoUrl="${VIDEO_URL}" title="See Cyrano in action" />`
  );

  // 9. Add InlineCTA after the second </section>
  let sectionCount = 0;
  content = content.replace(/<\/section>/g, (match) => {
    sectionCount++;
    if (sectionCount === 2) {
      return `</section>

          <InlineCTA
            theme={CYRANO_THEME}
            heading="See what your cameras are missing"
            body="Cyrano plugs into your existing DVR/NVR and starts monitoring in under 2 minutes. No camera replacement needed."
          />`;
    }
    return match;
  });

  // 10. Add StickyBottomCTA before </article> or before <GuideFooter>
  content = content.replace(
    /(\s*<GuideFooter theme=\{CYRANO_THEME\} \/>)/,
    `\n      <StickyBottomCTA theme={CYRANO_THEME} text="AI monitoring from $200/mo, no camera replacement" />$1`
  );

  // 11. Replace BOOKING_URL references that might remain
  content = content.replace(/\{BOOKING_URL\}/g, "{CYRANO_THEME.bookingUrl}");

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
    // Skip pages that already use the new components
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
