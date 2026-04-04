// batch_edit_linkedin.js — Edit multiple LinkedIn comments in sequence
// Runs inside Playwright MCP browser via browser_run_code.
//
// Set window.__batchEditParams = [ {postUrl, appendText, postId}, ... ] first,
// then run this file.
//
// Returns JSON array of results: [{postId, ok, newText?, error?}, ...]

async (page) => {
  const params = await page.evaluate(() => window.__batchEditParams);
  if (!params || !Array.isArray(params) || params.length === 0) {
    return JSON.stringify({ ok: false, error: 'Missing or empty __batchEditParams array' });
  }

  const results = [];

  for (const item of params) {
    const { postUrl, appendText, postId } = item;
    if (!postUrl || !appendText) {
      results.push({ postId, ok: false, error: 'missing_params' });
      continue;
    }

    try {
      // Step 1: Navigate to the post
      await page.goto(postUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(3000);

      // Step 2: Scroll down to load comments
      await page.evaluate(() => window.scrollBy(0, 1500));
      await page.waitForTimeout(2000);

      // Step 3: Find our comment's three-dot menu
      const menuButton = page.getByRole('button', { name: /View more options for Matthew/i }).first();

      try {
        await menuButton.waitFor({ timeout: 10000 });
      } catch {
        // Try scrolling more
        await page.evaluate(() => window.scrollBy(0, 2000));
        await page.waitForTimeout(2000);
        try {
          await menuButton.waitFor({ timeout: 5000 });
        } catch {
          results.push({ postId, ok: false, error: 'comment_not_found' });
          continue;
        }
      }

      await menuButton.click();
      await page.waitForTimeout(1000);

      // Step 4: Click "Edit" in the dropdown menu
      const editItem = page.getByRole('menuitem', { name: 'Edit' });
      try {
        await editItem.waitFor({ timeout: 5000 });
      } catch {
        results.push({ postId, ok: false, error: 'edit_option_not_found' });
        continue;
      }
      await editItem.click();
      await page.waitForTimeout(1500);

      // Step 5: Find the edit textbox and get current text
      const textboxes = page.getByRole('textbox', { name: 'Text editor for creating comment' });
      const count = await textboxes.count();

      if (count < 2) {
        results.push({ postId, ok: false, error: 'edit_textbox_not_found', textboxCount: count });
        // Press Escape to close any open menus
        await page.keyboard.press('Escape');
        await page.waitForTimeout(500);
        continue;
      }

      const editBox = textboxes.last();
      const currentText = await editBox.innerText();

      // Check if link already exists
      if (appendText.includes('http') || appendText.includes('://')) {
        const urlMatch = appendText.match(/https?:\/\/[^\s)]+/);
        if (urlMatch && currentText.includes(urlMatch[0])) {
          // Cancel the edit
          const cancelBtn = page.getByRole('button', { name: 'Cancel' });
          try { await cancelBtn.click(); } catch {}
          await page.waitForTimeout(500);
          results.push({ postId, ok: false, error: 'link_already_present' });
          continue;
        }
      }

      // Step 6: Append new text
      await editBox.click();
      await page.keyboard.press('Meta+End');
      await page.keyboard.press('End');
      await page.waitForTimeout(200);
      await page.keyboard.type(appendText);
      await page.waitForTimeout(500);

      // Step 7: Click "Save changes"
      const saveBtn = page.getByRole('button', { name: 'Save changes' });
      try {
        await saveBtn.waitFor({ timeout: 3000 });
        const isDisabled = await saveBtn.getAttribute('disabled');
        if (isDisabled !== null) {
          results.push({ postId, ok: false, error: 'save_button_disabled' });
          continue;
        }
      } catch {
        results.push({ postId, ok: false, error: 'save_button_not_found' });
        continue;
      }

      await saveBtn.click();
      await page.waitForTimeout(2000);

      // Step 8: Verify
      const editStillOpen = await page.getByRole('button', { name: 'Save changes' }).count();
      if (editStillOpen > 0) {
        results.push({ postId, ok: false, error: 'save_may_have_failed' });
        continue;
      }

      const newText = currentText.trimEnd() + appendText;
      results.push({ postId, ok: true, newText: newText.substring(0, 200) + '...' });

    } catch (e) {
      results.push({ postId, ok: false, error: e.message });
    }
  }

  return JSON.stringify(results);
}
