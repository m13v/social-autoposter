#!/usr/bin/env node
// Fix PieLine pages: add GuideNavbar, GuideFooter, StickyBottomCTA, ProofBanner, InlineCTA
import { readFileSync, writeFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

const PAGES_DIR = join(process.env.HOME, "pieline-phones/src/app/t");

function fixPage(filePath) {
  let content = readFileSync(filePath, "utf-8");

  // Skip if already has GuideNavbar in JSX (not just import)
  if (content.includes("<GuideNavbar")) return false;

  // 1. Wrap return: add fragment + GuideNavbar before <article
  content = content.replace(
    "return (\n    <article",
    "return (\n    <>\n      <GuideNavbar theme={PIELINE_THEME} />\n      <article"
  );

  // 2. Add ProofBanner after </header>
  if (!content.includes("<ProofBanner")) {
    content = content.replace(
      "</header>",
      `</header>

        <ProofBanner
          theme={PIELINE_THEME}
          quote="Mylapore (11 locations): projecting $500 additional revenue per location per day from eliminating phone bottleneck."
          source="Mylapore, Bay Area (11 locations)"
          metric="$500/day"
        />`
    );
  }

  // 3. Add InlineCTA after 2nd </section>
  if (!content.includes("<InlineCTA")) {
    let count = 0;
    content = content.replace(/<\/section>/g, (m) => {
      count++;
      if (count === 2) {
        return `</section>

          <InlineCTA
            theme={PIELINE_THEME}
            heading="Stop losing revenue to missed calls"
            body="PieLine answers every call 24/7, takes orders with 95%+ accuracy, and sends them straight to your POS."
          />`;
      }
      return m;
    });
  }

  // 4. Replace inline footer with GuideFooter + StickyBottomCTA and close fragment
  // Pattern: <footer class...>...</footer>\n    </article>\n  );\n}
  const footerPattern = /\s*<footer className="mt-16[\s\S]*?<\/footer>\s*\n\s*<\/article>\s*\n\s*\);\s*\n\s*\}/;
  if (footerPattern.test(content)) {
    content = content.replace(footerPattern, `
      </article>
      <StickyBottomCTA theme={PIELINE_THEME} text="AI phone answering from $350/mo, free 7-day trial" />
      <GuideFooter theme={PIELINE_THEME} />
    </>
  );
}`);
  } else {
    // No inline footer, just wrap closing
    content = content.replace(
      /\s*<\/article>\s*\n\s*\);\s*\n\s*\}/,
      `
      </article>
      <StickyBottomCTA theme={PIELINE_THEME} text="AI phone answering from $350/mo, free 7-day trial" />
      <GuideFooter theme={PIELINE_THEME} />
    </>
  );
}`
    );
  }

  writeFileSync(filePath, content);
  return true;
}

const dirs = readdirSync(PAGES_DIR).filter((d) => {
  const full = join(PAGES_DIR, d);
  return statSync(full).isDirectory() && d !== "[slug]";
});

let fixed = 0;
for (const dir of dirs) {
  const pagePath = join(PAGES_DIR, dir, "page.tsx");
  try {
    if (fixPage(pagePath)) {
      console.log(`FIXED: ${dir}`);
      fixed++;
    } else {
      console.log(`SKIP: ${dir}`);
    }
  } catch (e) {
    console.error(`ERROR: ${dir}: ${e.message}`);
  }
}
console.log(`\nFixed ${fixed} out of ${dirs.length} pages`);
