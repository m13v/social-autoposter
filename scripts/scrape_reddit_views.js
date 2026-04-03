// scrape_reddit_views.js — Scroll through a Reddit profile page and extract post view counts
// Runs inside the reddit-agent browser via browser_run_code.
// Caller navigates to the profile page first, then runs this script.
// Params via window.__params: { maxScrolls: 300 } (optional, defaults to 300)
// Returns JSON: { ok: true, total: N, scrolls: N, results: [{url, views}] }

async (page) => {
  try {
    const params = await page.evaluate(() => window.__params || {});
    const maxScrolls = params.maxScrolls || 300;

    await page.waitForTimeout(3000);
    const allResults = new Map();

    function extractCurrent() {
      return page.evaluate(() => {
        const results = [];
        document.querySelectorAll('article').forEach(article => {
          const links = article.querySelectorAll('a[href*="/comments/"]');
          let url = null;
          for (const link of links) {
            const href = link.getAttribute('href');
            if (href && href.includes('/comments/')) {
              if (!url || href.includes('/comment/')) url = href;
            }
          }
          let views = null;
          for (const el of article.querySelectorAll('*')) {
            const text = el.textContent.trim();
            const match = text.match(/^([\d,.]+)\s*([KkMm])?\s+views?$/);
            if (match) {
              let v = parseFloat(match[1].replace(/,/g, ''));
              if (match[2] && match[2].toLowerCase() === 'k') v *= 1000;
              if (match[2] && match[2].toLowerCase() === 'm') v *= 1000000;
              views = Math.round(v);
              break;
            }
          }
          if (url) {
            results.push({ url: url.startsWith('http') ? url : 'https://www.reddit.com' + url, views });
          }
        });
        return results;
      });
    }

    let items = await extractCurrent();
    for (const item of items) allResults.set(item.url, item.views);

    let previousHeight = 0, sameHeightCount = 0, scrollCount = 0;
    while (sameHeightCount < 4 && scrollCount < maxScrolls) {
      const currentHeight = await page.evaluate(() => document.body.scrollHeight);
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(2000);
      items = await extractCurrent();
      for (const item of items) allResults.set(item.url, item.views);
      if (currentHeight === previousHeight) sameHeightCount++;
      else sameHeightCount = 0;
      previousHeight = currentHeight;
      scrollCount++;
    }

    const resultsArray = Array.from(allResults.entries()).map(([url, views]) => ({ url, views }));
    return JSON.stringify({ ok: true, total: resultsArray.length, scrolls: scrollCount, results: resultsArray });
  } catch (e) {
    return JSON.stringify({ ok: false, error: e.message });
  }
}
