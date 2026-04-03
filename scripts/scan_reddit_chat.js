// scan_reddit_chat.js — Extract Reddit Chat conversations with unread indicators
// Runs inside the reddit-agent browser via browser_run_code.
// Returns JSON with chat conversations from the sidebar.
// Reddit Chat SPA doesn't render via CDP but works in the MCP browser agent.

async (page) => {
  try {
    // 1. Navigate to Reddit Chat
    await page.goto('https://www.reddit.com/chat', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(4000);

    // 2. Extract conversations from the sidebar
    const conversations = await page.evaluate(() => {
      const results = [];

      // Strategy 1: Find chat links in the sidebar
      const links = document.querySelectorAll('a[href*="/chat/"]');

      for (const link of links) {
        const href = link.getAttribute('href') || '';
        if (!href.includes('/chat/')) continue;
        // Skip non-conversation links (create, settings, the /chat/ root itself)
        if (href === '/chat' || href === '/chat/' || href.includes('create') || href.includes('settings')) continue;

        const chatUrl = href.startsWith('http')
          ? href
          : 'https://www.reddit.com' + href;

        // Extract info from aria-label if available
        // Reddit Chat uses aria-labels like: "USERNAME said TIME, TEXT, N replies, N reactions"
        const ariaLabel = link.getAttribute('aria-label') || '';
        let author = '';
        let preview = '';

        if (ariaLabel) {
          const ariaMatch = ariaLabel.match(/^(.+?)\s+said\s+/);
          if (ariaMatch) {
            author = ariaMatch[1].trim();
          }
          // Extract preview text after the time portion
          const previewMatch = ariaLabel.match(/said\s+[^,]+,\s*(.+?)(?:,\s*\d+\s+repl|,\s*\d+\s+react|$)/);
          if (previewMatch) {
            preview = previewMatch[1].trim().substring(0, 200);
          }
        }

        // Fallback: extract from text content and child elements
        if (!author) {
          const spans = link.querySelectorAll('span, div, p');
          for (const s of spans) {
            const t = (s.textContent || '').trim();
            // Match "Username: preview text" pattern
            const colonMatch = t.match(/^(\S+):\s*(.+)/);
            if (colonMatch && colonMatch[1].length < 30) {
              author = colonMatch[1];
              if (!preview) preview = colonMatch[2].substring(0, 200);
              break;
            }
          }
        }

        // Fallback: use the link text or first meaningful child text
        if (!author) {
          const textContent = (link.textContent || '').trim();
          if (textContent.length > 0 && textContent.length < 100) {
            author = textContent.split('\n')[0].trim();
          }
        }

        // Skip if we couldn't identify the conversation
        if (!author || author.length < 1) continue;

        // Check for unread indicators
        // Look for unread badge elements, notification dots, or aria attributes
        let hasUnread = false;

        // Check within the link for unread indicators
        const unreadByAria = link.querySelector('[aria-label*="unread"], [aria-label*="Unread"], [aria-label*="new message"]');
        if (unreadByAria) hasUnread = true;

        // Check for notification badge/dot (often a small circle or count)
        if (!hasUnread) {
          const badges = link.querySelectorAll('[class*="unread"], [class*="badge"], [class*="notification"], [class*="dot"]');
          if (badges.length > 0) hasUnread = true;
        }

        // Check parent element for unread styling
        if (!hasUnread && link.parentElement) {
          const parentAria = link.parentElement.querySelector('[aria-label*="unread"], [aria-label*="Unread"]');
          if (parentAria) hasUnread = true;
          const parentBadge = link.parentElement.querySelector('[class*="unread"], [class*="badge"]');
          if (parentBadge) hasUnread = true;
        }

        // Check for bold text (common unread indicator)
        if (!hasUnread) {
          const boldEls = link.querySelectorAll('b, strong, [style*="font-weight"]');
          if (boldEls.length > 0) hasUnread = true;
        }

        results.push({
          author: author.substring(0, 80),
          preview: preview.substring(0, 200),
          chat_url: chatUrl,
          has_unread: hasUnread,
        });
      }

      // Deduplicate by chat_url
      const seen = new Set();
      const unique = [];
      for (const r of results) {
        if (!seen.has(r.chat_url)) {
          seen.add(r.chat_url);
          unique.push(r);
        }
      }

      return unique;
    });

    return JSON.stringify({ ok: true, conversations });
  } catch (e) {
    return JSON.stringify({ ok: false, error: e.message });
  }
}
