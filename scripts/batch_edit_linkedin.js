// batch_edit_linkedin.js — Edit multiple LinkedIn comments in sequence
// Set window.__batchEditParams = [ { postUrl, appendText, postId }, ... ]
// Returns JSON array of results: [ { postId, ok, newText?, error? }, ... ]

async (page) => {
  const edits = await page.evaluate(() => window.__batchEditParams);
  if (!edits || !Array.isArray(edits) || edits.length === 0) {
    return JSON.stringify({ ok: false, error: 'Missing __batchEditParams array' });
  }

  const results = [];

  for (const edit of edits) {
    const { postUrl, appendText, postId } = edit;
    if (!postUrl || !appendText) {
      results.push({ postId, ok: false, error: 'missing_params' });
      continue;
    }

    try {
      // Navigate to the post
      await page.goto(postUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(3000);

      // Scroll down to load comments
      await page.evaluate(() => window.scrollBy(0, 1500));
      await page.waitForTimeout(2000);

      // Find our comment's three-dot menu
      const menuButton = page.getByRole('button', { name: /View more options for Matthew/i }).first();

      let found = false;
      try {
        await menuButton.waitFor({ timeout: 10000 });
        found = true;
      } catch {
        await page.evaluate(() => window.scrollBy(0, 2000));
        await page.waitForTimeout(2000);
        try {
          await menuButton.waitFor({ timeout: 5000 });
          found = true;
        } catch {
          try {
            const loadMore = page.getByRole('button', { name: /load more comments/i });
            await loadMore.click();
            await page.waitForTimeout(3000);
            await menuButton.waitFor({ timeout: 5000 });
            found = true;
          } catch {}
        }
      }

      if (!found) {
        results.push({ postId, ok: false, error: 'comment_not_found' });
        continue;
      }

      await menuButton.click();
      await page.waitForTimeout(1000);

      // Click "Edit"
      const editItem = page.getByRole('menuitem', { name: 'Edit' });
      try {
        await editItem.waitFor({ timeout: 5000 });
      } catch {
        results.push({ postId, ok: false, error: 'edit_option_not_found' });
        continue;
      }
      await editItem.click();
      await page.waitForTimeout(1500);

      // Find edit textbox
      const textboxes = page.getByRole('textbox', { name: 'Text editor for creating comment' });
      const count = await textboxes.count();
      if (count < 2) {
        results.push({ postId, ok: false, error: 'edit_textbox_not_found', textboxCount: count });
        continue;
      }

      const editBox = textboxes.last();
      const currentText = await editBox.innerText();

      // Check if link already present
      if (appendText.includes('http') || appendText.includes('://')) {
        const urlMatch = appendText.match(/https?:\/\/[^\s)]+/);
        if (urlMatch && currentText.includes(urlMatch[0])) {
          results.push({ postId, ok: false, error: 'link_already_present' });
          continue;
        }
      }

      // Append text
      await editBox.click();
      await page.keyboard.press('Meta+End');
      await page.keyboard.press('End');
      await page.waitForTimeout(200);
      await page.keyboard.type(appendText);
      await page.waitForTimeout(500);

      // Save
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
      await page.waitForTimeout(2500);

      const newText = currentText.trimEnd() + appendText;
      results.push({ postId, ok: true, newText });

    } catch (e) {
      results.push({ postId, ok: false, error: e.message });
    }
  }

  return JSON.stringify(results);
}
