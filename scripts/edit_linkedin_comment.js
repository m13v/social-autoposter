// edit_linkedin_comment.js — Edit a LinkedIn comment programmatically
// Runs inside the linkedin-agent browser via browser_run_code.
//
// Usage: browser_run_code with filename parameter, after setting window.__editParams:
//   await page.evaluate(() => {
//     window.__editParams = {
//       postUrl: "https://www.linkedin.com/feed/update/urn:li:activity:1234/",
//       appendText: "\n\nI've been exploring a tool for this - https://example.com"
//     };
//   });
//   Then run this file via browser_run_code filename parameter.
//
// Returns JSON: { ok: true, newText: "..." } or { ok: false, error: "..." }

async (page) => {
  const params = await page.evaluate(() => window.__editParams);
  if (!params || !params.postUrl || !params.appendText) {
    return JSON.stringify({ ok: false, error: 'Missing __editParams (postUrl, appendText)' });
  }

  try {
    // Step 1: Navigate to the post
    await page.goto(params.postUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(3000);

    // Step 2: Scroll down to load comments
    await page.evaluate(() => window.scrollBy(0, 1500));
    await page.waitForTimeout(2000);

    // Step 3: Find our comment's three-dot menu
    // Look for "View more options for Matthew Diakonov's comment"
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
        return JSON.stringify({ ok: false, error: 'comment_not_found' });
      }
    }

    await menuButton.click();
    await page.waitForTimeout(1000);

    // Step 4: Click "Edit" in the dropdown menu
    const editItem = page.getByRole('menuitem', { name: 'Edit' });
    try {
      await editItem.waitFor({ timeout: 5000 });
    } catch {
      return JSON.stringify({ ok: false, error: 'edit_option_not_found' });
    }
    await editItem.click();
    await page.waitForTimeout(1500);

    // Step 5: Find the edit textbox and get current text
    // After clicking Edit, a new textbox appears near the Save/Cancel buttons.
    // It's always the LAST "Text editor for creating comment" textbox on the page
    // (the first one is the "Add a comment" box at the top).
    const textboxes = page.getByRole('textbox', { name: 'Text editor for creating comment' });
    const count = await textboxes.count();

    if (count < 2) {
      return JSON.stringify({ ok: false, error: 'edit_textbox_not_found', textboxCount: count });
    }

    // The edit box is the last one (appears after clicking Edit)
    const editBox = textboxes.last();
    const currentText = await editBox.innerText();

    // Check if link already exists in the comment
    if (params.appendText.includes('http') || params.appendText.includes('://')) {
      const urlMatch = params.appendText.match(/https?:\/\/[^\s)]+/);
      if (urlMatch && currentText.includes(urlMatch[0])) {
        return JSON.stringify({ ok: false, error: 'link_already_present', currentText });
      }
    }

    // Step 6: Append new text
    const newText = currentText.trimEnd() + params.appendText;

    // Click into the edit box, go to end, then type the appended text
    await editBox.click();
    await page.keyboard.press('Meta+End');  // Go to very end
    await page.keyboard.press('End');
    await page.waitForTimeout(200);
    await page.keyboard.type(params.appendText);
    await page.waitForTimeout(500);

    // Step 7: Click "Save changes"
    const saveBtn = page.getByRole('button', { name: 'Save changes' });
    try {
      await saveBtn.waitFor({ timeout: 3000 });
      // Check if button is enabled
      const isDisabled = await saveBtn.getAttribute('disabled');
      if (isDisabled !== null) {
        return JSON.stringify({ ok: false, error: 'save_button_disabled', currentText, newText });
      }
    } catch {
      return JSON.stringify({ ok: false, error: 'save_button_not_found' });
    }

    await saveBtn.click();
    await page.waitForTimeout(2000);

    // Step 8: Verify — check that Edit menu is no longer open (comment saved)
    const editStillOpen = await page.getByRole('button', { name: 'Save changes' }).count();
    if (editStillOpen > 0) {
      return JSON.stringify({ ok: false, error: 'save_may_have_failed', newText });
    }

    return JSON.stringify({ ok: true, newText });

  } catch (e) {
    return JSON.stringify({ ok: false, error: e.message });
  }
}
