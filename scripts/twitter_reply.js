// Twitter Reply Script - posts a reply and captures the reply URL via CreateTweet interception.
// Usage: mcp__twitter-agent__browser_run_code with filename parameter
// Expects global vars TWEET_URL and REPLY_TEXT to be set before calling.
// Returns JSON: {ok, tweet_url, reply_url, verified, error}

async (page) => {
  const tweetUrl = globalThis.TWEET_URL;
  const replyText = globalThis.REPLY_TEXT;

  if (!tweetUrl || !replyText) {
    return JSON.stringify({ ok: false, error: 'TWEET_URL and REPLY_TEXT must be set via browser_evaluate before calling this script' });
  }

  let capturedReplyId = null;

  // Set up route interception for CreateTweet
  await page.route('**/CreateTweet**', async (route) => {
    const response = await route.fetch();
    try {
      const body = JSON.parse(await response.text());
      const restId = body?.data?.create_tweet?.tweet_results?.result?.rest_id;
      if (restId) capturedReplyId = restId;
      await route.fulfill({ response });
    } catch {
      await route.fulfill({ response });
    }
  });

  try {
    // Navigate to the tweet
    await page.goto(tweetUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(5000);

    // Check if page exists
    const mainText = await page.textContent('main').catch(() => '');
    if (mainText.toLowerCase().includes("this page doesn't exist")) {
      await page.unroute('**/CreateTweet**');
      return JSON.stringify({ ok: false, error: 'tweet_not_found', tweet_url: tweetUrl });
    }

    // Find the reply textbox
    let replyBox;
    try {
      replyBox = page.getByRole('textbox', { name: 'Post text' });
      await replyBox.waitFor({ timeout: 10000 });
    } catch {
      await page.evaluate(() => window.scrollBy(0, 500));
      await page.waitForTimeout(2000);
      try {
        replyBox = page.getByRole('textbox', { name: 'Post text' });
        await replyBox.waitFor({ timeout: 5000 });
      } catch {
        await page.unroute('**/CreateTweet**');
        return JSON.stringify({ ok: false, error: 'reply_box_not_found', tweet_url: tweetUrl });
      }
    }

    // Type the reply
    await replyBox.click();
    await page.waitForTimeout(500);
    await page.keyboard.type(replyText, { delay: 10 });
    await page.waitForTimeout(1000);

    // Click Reply button
    try {
      const replyBtn = page.getByRole('button', { name: 'Reply' }).last();
      await replyBtn.waitFor({ timeout: 5000 });
      await replyBtn.click();
    } catch {
      await page.keyboard.press('Control+Enter');
    }

    // Wait for the CreateTweet response
    await page.waitForTimeout(5000);

    // Verify: check if reply box is cleared
    let verified = false;
    try {
      const boxText = await replyBox.textContent();
      verified = !boxText || boxText.trim().length === 0 || !boxText.includes(replyText);
    } catch {
      verified = true;
    }

    // Clean up route
    await page.unroute('**/CreateTweet**');

    const replyUrl = capturedReplyId
      ? `https://x.com/m13v_/status/${capturedReplyId}`
      : null;

    return JSON.stringify({
      ok: true,
      tweet_url: tweetUrl,
      reply_url: replyUrl,
      verified
    });

  } catch (err) {
    await page.unroute('**/CreateTweet**').catch(() => {});
    return JSON.stringify({ ok: false, error: err.message, tweet_url: tweetUrl });
  }
};
