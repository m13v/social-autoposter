// scan_linkedin_notifications.js — Extract LinkedIn notifications via internal API
// Runs inside the linkedin-agent browser via browser_run_code.
// Returns JSON with actionable notifications (replies, comments, mentions).
// Called by scan_linkedin_notifications.py which handles DB insertion.

async (page) => {
  const result = await page.evaluate(async () => {
    const csrfToken = (document.cookie.match(/JSESSIONID="?([^";]+)/) || [])[1] || '';
    if (!csrfToken) return JSON.stringify({ error: 'No CSRF token - not logged in' });

    const headers = {
      'csrf-token': csrfToken,
      'accept': 'application/vnd.linkedin.normalized+json+2.1',
      'x-restli-protocol-version': '2.0.0',
    };

    const actionableTypes = new Set([
      'REPLIED_TO_YOUR_COMMENT',
      'COMMENTED_ON_YOUR_UPDATE',
      'COMMENTED_ON_YOUR_POST',
      'MENTIONED_YOU_IN_A_COMMENT',
      'MENTIONED_YOU_IN_THIS',
    ]);

    const allNotifications = [];
    const profiles = {};

    // Paginate through notifications (up to 100)
    for (let start = 0; start < 100; start += 25) {
      const resp = await fetch(
        `/voyager/api/voyagerIdentityDashNotificationCards?decorationId=com.linkedin.voyager.dash.deco.identity.notifications.CardsCollection-80&count=25&filterUrn=urn%3Ali%3Afsd_notificationFilter%3AALL&q=notifications&start=${start}`,
        { headers }
      );
      if (resp.status !== 200) {
        if (start === 0) return JSON.stringify({ error: `API returned ${resp.status}` });
        break;
      }

      const data = await resp.json();
      const included = data.included || [];

      // Collect profile names
      included
        .filter(e => e.$type === 'com.linkedin.voyager.dash.identity.profile.Profile')
        .forEach(p => {
          const name = (p.profilePicture && p.profilePicture.a11yText) || '';
          if (name) profiles[p.entityUrn] = name;
        });

      // Process notification cards
      included
        .filter(e => e.$type === 'com.linkedin.voyager.dash.identity.notifications.Card')
        .forEach(card => {
          const objUrn = card.objectUrn || '';
          const typeMatch = objUrn.match(/,([A-Z_]+),/) || objUrn.match(/,([A-Z_]+)\)/);
          const notifType = typeMatch ? typeMatch[1] : 'UNKNOWN';

          if (!actionableTypes.has(notifType)) return;

          // Extract comment URN
          const commentMatch = objUrn.match(/urn:li:comment:\([^)]+\)/);
          const commentUrn = commentMatch ? commentMatch[0] : '';

          // Extract activity/ugcPost ID from comment URN or cardAction
          let activityId = '';
          const actMatch = commentUrn.match(/activity:(\d+)/) || commentUrn.match(/ugcPost:(\d+)/);
          if (actMatch) activityId = actMatch[1];

          // Author name from headline
          const headline = (card.headline && card.headline.text) || '';
          const authorMatch = headline.match(/^(.+?)\s+(replied|commented|mentioned)/);
          const authorName = authorMatch ? authorMatch[1] : '';

          // Author profile URL from headerImage
          const profileUrl = (card.headerImage && card.headerImage.actionTarget) || '';

          // Navigation URL (link to the comment)
          const navUrl = (card.cardAction && card.cardAction.actionTarget) || '';

          // Original post content from contentSecondaryText
          const secondaryTexts = card.contentSecondaryText || [];
          const postContent = secondaryTexts.map(t => t.text || '').join(' ').substring(0, 500);

          // Content primary text (the reply/comment text itself)
          const primaryTexts = card.contentPrimaryText || [];
          let commentText = '';
          if (Array.isArray(primaryTexts)) {
            commentText = primaryTexts.map(t => t.text || '').join(' ').substring(0, 500);
          } else if (primaryTexts && primaryTexts.text) {
            commentText = primaryTexts.text.substring(0, 500);
          }

          allNotifications.push({
            type: notifType,
            authorName,
            profileUrl,
            commentUrn,
            activityId,
            navigationUrl: navUrl,
            headline,
            commentText,
            postContent,
            publishedAt: card.publishedAt || 0,
            entityUrn: card.entityUrn || '',
          });
        });
    }

    return JSON.stringify({
      count: allNotifications.length,
      notifications: allNotifications,
    });
  });

  return result;
}
