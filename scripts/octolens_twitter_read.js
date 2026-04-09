const { chromium } = require('playwright');

const urls = process.argv.slice(2);
if (!urls.length) { console.error('Usage: node octolens_twitter_read.js <url1> <url2> ...'); process.exit(1); }

(async () => {
  const browser = await chromium.launchPersistentContext(
    '/Users/matthewdi/.claude/browser-profiles/twitter',
    {
      headless: false,
      viewport: { width: 911, height: 1016 },
      args: ['--window-position=3042,-1032', '--window-size=911,1016']
    }
  );

  for (const url of urls) {
    const page = await browser.newPage();
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
      await page.waitForTimeout(3000);

      // Get the main tweet and replies
      const content = await page.evaluate(() => {
        const tweets = document.querySelectorAll('[data-testid="tweet"]');
        const results = [];
        for (let i = 0; i < Math.min(tweets.length, 10); i++) {
          const t = tweets[i];
          const author = t.querySelector('[data-testid="User-Name"]')?.textContent || '';
          const text = t.querySelector('[data-testid="tweetText"]')?.textContent || '';
          const likes = t.querySelector('[data-testid="like"]')?.getAttribute('aria-label') || '';
          const replies = t.querySelector('[data-testid="reply"]')?.getAttribute('aria-label') || '';
          results.push({ author, text, likes, replies });
        }
        return results;
      });

      console.log(`\n=== THREAD: ${url} ===`);
      content.forEach((t, i) => {
        console.log(`[${i}] ${t.author}`);
        console.log(`    ${t.text}`);
        console.log(`    Likes: ${t.likes} | Replies: ${t.replies}`);
      });
    } catch (e) {
      console.error(`Error loading ${url}: ${e.message}`);
    }
    await page.close();
  }

  await browser.close();
})();
