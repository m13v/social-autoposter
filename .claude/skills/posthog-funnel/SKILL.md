# PostHog Product Funnel Lookup

Look up the full marketing funnel for a product tracked in the S4L PostHog account.

## Usage

```
/posthog-funnel <product>
```

Products: `cyrano`, `pieline` (mapped by domain below).

---

## Setup

**PostHog project:** S4L, ID `330744`, host `us.posthog.com`

**Personal API key:** stored in macOS keychain as `PostHog-Personal-API-Key-m13v`

```bash
POSTHOG_KEY=$(security find-generic-password -s "PostHog-Personal-API-Key-m13v" -w)
```

Also available as `POSTHOG_PERSONAL_API_KEY` in `~/social-autoposter/.env`.

**API base:** `https://us.posthog.com/api/environments/330744`

**Auth header:** `Authorization: Bearer $POSTHOG_KEY`

---

## Product-to-Domain Mapping

| Product | Domain | Cal slug |
|---------|--------|----------|
| Cyrano | `apartment-security-cameras.com` | `cyrano` |
| PieLine | `pieline.dev` | `pieline` |

---

## Funnel Steps

Run all 4 steps, then present the summary table at the end.

### Step 1: Total Pageviews & Unique Visitors

```bash
DOMAIN="apartment-security-cameras.com"  # or pieline.dev
POSTHOG_KEY=$(security find-generic-password -s "PostHog-Personal-API-Key-m13v" -w)

# Paginate to get all pageviews
curl -s "$API_BASE/events/?event=\$pageview&limit=200&properties=%5B%7B%22key%22%3A%22%24host%22%2C%22value%22%3A%22$DOMAIN%22%2C%22operator%22%3A%22exact%22%2C%22type%22%3A%22event%22%7D%5D" \
  -H "Authorization: Bearer $POSTHOG_KEY"
```

Paginate using the `next` URL until empty. Count:
- Total pageviews
- Unique visitors (distinct `distinct_id` values)
- Breakdown by page path
- Breakdown by referrer (where traffic comes from)
- Breakdown by date

### Step 2: CTA Clicks

```bash
curl -s "$API_BASE/events/?event=cta_click&limit=200&properties=%5B%7B%22key%22%3A%22%24host%22%2C%22value%22%3A%22$DOMAIN%22%2C%22operator%22%3A%22exact%22%2C%22type%22%3A%22event%22%7D%5D" \
  -H "Authorization: Bearer $POSTHOG_KEY"
```

Count total CTA clicks. Note the `text` property for button label (e.g. "Book a Free Demo"). Break down by page where clicked.

### Step 3: Lead Form Submissions

```bash
curl -s "$API_BASE/events/?event=get_leads_modal_submit&limit=200&properties=%5B%7B%22key%22%3A%22%24host%22%2C%22value%22%3A%22$DOMAIN%22%2C%22operator%22%3A%22exact%22%2C%22type%22%3A%22event%22%7D%5D" \
  -H "Authorization: Bearer $POSTHOG_KEY"
```

Count lead submissions. Note referrer source for each.

### Step 4: Calendar Bookings

```bash
# Filter by client_slug to get product-specific bookings
curl -s "$API_BASE/events/?event=cal_booking&limit=200" \
  -H "Authorization: Bearer $POSTHOG_KEY"
```

Filter results where `properties.client_slug` matches the product (e.g. `cyrano` or `pieline`). **Exclude test bookings** - filter out any where `attendee_name` contains "TEST" or "test" (case-insensitive).

Count real bookings only.

---

## Other Useful Events

Query these the same way (filter by `$host` = domain):

| Event | What it tracks |
|-------|---------------|
| `nav_click` | Navigation link clicks. `text` property has the link name |
| `post_click` | Blog/content post clicks |
| `section_viewed` | Which page sections users scroll to |
| `cpc_calculator_interaction` | ROI calculator usage |
| `video_play` | Video plays |

---

## Output Format

Present results as a funnel summary:

```
## [Product] Funnel Report ([date range])

| Stage | Count | Conversion |
|-------|-------|------------|
| Pageviews | X | - |
| Unique visitors | X | - |
| CTA clicks | X | X% of visitors |
| Lead form submissions | X | X% of visitors |
| Demo bookings (real) | X | X% of visitors |

### Traffic Sources
- [breakdown by referrer]

### Top Pages
- [breakdown by path]

### CTA Click Details
- [which buttons, on which pages]
```
