# Jungle AI: get programmatic GA4 access

Goal: read signup attribution by `utm_source` from Jungle's GA4 property,
without depending on Jungle to pull manual reports.

Site: https://jungleai.com  (signup on https://app.jungleai.com)
Property: GA4 measurement IDs spotted on site = G-0LQB6WDPNK (main),
also FX0SX0G8KF and 9F4001PQMX. Ask Jungle which property is the
production one before requesting access.

---

## Step 1. Email/Slack to Jungle

> Hi, we're sending paid/organic traffic to jungleai.com with UTM tags
> and want to monitor signup attribution programmatically. Could you
> add the following service account as a **Viewer** on your GA4 property?
>
> Service account: `ga4-jungle-reader@<your-gcp-project>.iam.gserviceaccount.com`
>
> Steps on your side:
> 1. GA4 -> Admin (bottom left gear)
> 2. Property column -> Property access management
> 3. + button (top right) -> Add users
> 4. Email: `<paste SA email>`
> 5. Role: Viewer
> 6. Click Add
>
> Also share your GA4 Property ID (Admin -> Property settings, top right,
> 9-10 digit number) and the event name your signup flow fires
> (e.g. `sign_up`, `signup_completed`).

---

## Step 2. Provision the service account on our side (one-time)

Replace `<your-gcp-project>` with your actual GCP project (likely m13v.com org).

```bash
PROJECT=<your-gcp-project>

gcloud services enable analyticsdata.googleapis.com --project=$PROJECT

gcloud iam service-accounts create ga4-jungle-reader \
  --project=$PROJECT \
  --display-name="GA4 reader for Jungle AI"

gcloud iam service-accounts keys create ~/.config/ga4-jungle.json \
  --iam-account=ga4-jungle-reader@$PROJECT.iam.gserviceaccount.com
```

Save `~/.config/ga4-jungle.json` securely. Treat it like a password.

---

## Step 3. Verify access

After Jungle confirms they added the SA, run:

```bash
python3 -c "
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric
import os
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/Users/matthewdi/.config/ga4-jungle.json'
c = BetaAnalyticsDataClient()
r = c.run_report(RunReportRequest(
    property='properties/<PROPERTY_ID>',
    date_ranges=[DateRange(start_date='7daysAgo', end_date='today')],
    dimensions=[Dimension(name='sessionSource')],
    metrics=[Metric(name='sessions')],
))
for row in r.rows:
    print(row.dimension_values[0].value, row.metric_values[0].value)
"
```

If you see a list of sources with session counts, access works.

---

## Step 4. Signup attribution query

Once Jungle confirms the signup event name (assume `sign_up` below),
this is the production query.

```python
# scripts/jungleai_ga4_pull.py
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric, Filter, FilterExpression
)
import os, json

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/Users/matthewdi/.config/ga4-jungle.json'

PROPERTY_ID = '<PROPERTY_ID>'        # from Jungle
SIGNUP_EVENT = 'sign_up'             # confirm with Jungle
OUR_UTM_SOURCE = '<your_utm_source>' # what we tag traffic with

client = BetaAnalyticsDataClient()

resp = client.run_report(RunReportRequest(
    property=f'properties/{PROPERTY_ID}',
    date_ranges=[DateRange(start_date='30daysAgo', end_date='today')],
    dimensions=[
        Dimension(name='sessionSource'),
        Dimension(name='sessionMedium'),
        Dimension(name='sessionCampaignName'),
        Dimension(name='date'),
    ],
    metrics=[
        Metric(name='sessions'),
        Metric(name='totalUsers'),
        Metric(name='eventCount'),
    ],
    dimension_filter=FilterExpression(
        and_group={'expressions': [
            FilterExpression(filter=Filter(
                field_name='sessionSource',
                string_filter=Filter.StringFilter(value=OUR_UTM_SOURCE),
            )),
            FilterExpression(filter=Filter(
                field_name='eventName',
                string_filter=Filter.StringFilter(value=SIGNUP_EVENT),
            )),
        ]},
    ),
))

rows = []
for row in resp.rows:
    rows.append({
        'source': row.dimension_values[0].value,
        'medium': row.dimension_values[1].value,
        'campaign': row.dimension_values[2].value,
        'date': row.dimension_values[3].value,
        'sessions': row.metric_values[0].value,
        'users': row.metric_values[1].value,
        'signups': row.metric_values[2].value,
    })

print(json.dumps(rows, indent=2))
```

Run on cron, dump to Neon or stdout, post to Slack. Done.

---

## Notes / gotchas

- GA4 has a **48-hour processing delay** for some dimensions. Yesterday's
  numbers can shift. For "today" expect partial data.
- `sessionSource` reflects last-non-direct attribution by default.
  A user who first arrived via our UTM, left, returned direct, then
  signed up will still attribute to our UTM within 90 days. Good.
- If Jungle uses **first-click attribution** in their reports,
  switch dimensions to `firstUserSource` / `firstUserMedium` /
  `firstUserCampaignName` for matching numbers.
- Service account auth has no token expiry. Set it once, runs forever.
- 25k-token quota per day per property. Trivial for our scale.
- If they refuse SA access and only offer human Viewer access on
  `i@m13v.com`, switch to OAuth user creds. More fragile, requires
  re-auth periodically.
- Backup plan if GA4 access doesn't happen: ask for a weekly emailed
  CSV export filtered to our utm_source.
