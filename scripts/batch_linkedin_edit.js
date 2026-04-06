// batch_linkedin_edit.js - Process a single LinkedIn edit
// Usage: Set window.__editParams then run this file
// Same as edit_linkedin_comment.js but exported for batch use

async (page) => {
  const params = await page.evaluate(() => window.__editParams);
  if (!params || !params.postUrl || !params.appendText) {
    return JSON.stringify({ ok: false, error: 'Missing __editParams (postUrl, appendText)' });
  }

  try {
    await page.goto(params.postUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(3000);
    await page.evaluate(() => window.scrollBy(0, 1500));
    await page.waitForTimeout(2000);

    const menuButton = page.getByRole('button', { name: /View more options for Matthew/i }).first();
    try {
      await menuButton.waitFor({ timeout: 10000 });
    } catch {
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

    const editItem = page.getByRole('menuitem', { name: 'Edit' });
    try {
      await editItem.waitFor({ timeout: 5000 });
    } catch {
      return JSON.stringify({ ok: false, error: 'edit_option_not_found' });
    }
    await editItem.click();
    await page.waitForTimeout(1500);

    const textboxes = page.getByRole('textbox', { name: 'Text editor for creating comment' });
    const count = await textboxes.count();
    if (count < 2) {
      return JSON.stringify({ ok: false, error: 'edit_textbox_not_found', textboxCount: count });
    }

    const editBox = textboxes.last();
    const currentText = await editBox.innerText();

    if (params.appendText.includes('http') || params.appendText.includes('://')) {
      const urlMatch = params.appendText.match(/https?:\/\/[^\s)]+/);
      if (urlMatch && currentText.includes(urlMatch[0])) {
        return JSON.stringify({ ok: false, error: 'link_already_present', currentText });
      }
    }

    await editBox.click();
    await page.keyboard.press('Meta+End');
    await page.keyboard.press('End');
    await page.waitForTimeout(200);
    await page.keyboard.type(params.appendText);
    await page.waitForTimeout(500);

    const saveBtn = page.getByRole('button', { name: 'Save changes' });
    try {
      await saveBtn.waitFor({ timeout: 3000 });
      const isDisabled = await saveBtn.getAttribute('disabled');
      if (isDisabled !== null) {
        return JSON.stringify({ ok: false, error: 'save_button_disabled' });
      }
    } catch {
      return JSON.stringify({ ok: false, error: 'save_button_not_found' });
    }

    await saveBtn.click();
    await page.waitForTimeout(2000);

    const editStillOpen = await page.getByRole('button', { name: 'Save changes' }).count();
    if (editStillOpen > 0) {
      return JSON.stringify({ ok: false, error: 'save_may_have_failed' });
    }

    return JSON.stringify({ ok: true });
  } catch (e) {
    return JSON.stringify({ ok: false, error: e.message });
  }
}
