import { Stagehand } from "@browserbasehq/stagehand";
import { z } from "zod";

const QUERIES = [
  "best AI tool to auto-post on reddit twitter linkedin for solo founders",
  "how to automate SEO landing page generation with AI",
  "AI agent that engages prospects in DM conversations",
];

const BRAND_TERMS = ["social-autoposter", "social autoposter", "m13v"];

const CitationSchema = z.object({
  citations: z.array(
    z.object({
      title: z.string(),
      url: z.string(),
    })
  ),
});

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pollUntilCitations(
  stagehand: Stagehand,
  maxWaitMs: number,
  pollIntervalMs: number
) {
  const deadline = Date.now() + maxWaitMs;
  let last: z.infer<typeof CitationSchema> = { citations: [] };
  while (Date.now() < deadline) {
    last = await stagehand.extract(
      "extract every source citation/source chip shown alongside the AI answer. include the visible title (or hostname if no title) and the full URL.",
      CitationSchema
    );
    if (last.citations.length > 0) return last;
    await sleep(pollIntervalMs);
  }
  return last;
}

async function checkPerplexity(stagehand: Stagehand, query: string) {
  const page = stagehand.context.activePage();
  if (!page) throw new Error("no active page");

  await page.goto("https://www.perplexity.ai/", { waitUntil: "domcontentloaded" });

  await stagehand.act(`click the main search/ask input box on the page`);
  await stagehand.act(`type "${query}" into the focused search/ask input`);
  await page.keyPress("Enter");

  await sleep(6000);
  console.error(`  url after submit: ${page.url()}`);
  const result = await pollUntilCitations(stagehand, 45000, 4000);

  if (result.citations.length === 0) {
    const fs = await import("node:fs/promises");
    await fs.mkdir("results", { recursive: true });
    const safe = query.replace(/[^a-z0-9]+/gi, "_").slice(0, 60);
    const path = `results/debug_${safe}.png`;
    await page.screenshot({ path });
    console.error(`  saved debug screenshot: ${path}`);
  }

  const citedHits = result.citations.filter((c) => {
    const blob = `${c.title} ${c.url}`.toLowerCase();
    return BRAND_TERMS.some((b) => blob.includes(b.toLowerCase()));
  });

  return {
    engine: "perplexity",
    query,
    brand_cited: citedHits.length > 0,
    citation_count: result.citations.length,
    brand_hits: citedHits,
    citations: result.citations,
  };
}

async function main() {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    console.error("ANTHROPIC_API_KEY is required (Stagehand uses an LLM for extract/act).");
    process.exit(1);
  }

  const stagehand = new Stagehand({
    env: "LOCAL",
    model: { modelName: "anthropic/claude-sonnet-4-6", apiKey },
    localBrowserLaunchOptions: { headless: false },
    verbose: 1,
  });

  await stagehand.init();

  try {
    for (const q of QUERIES) {
      try {
        const r = await checkPerplexity(stagehand, q);
        console.log(JSON.stringify(r, null, 2));
      } catch (err) {
        console.error(`query failed: ${q}`, err);
      }
    }
  } finally {
    await stagehand.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
