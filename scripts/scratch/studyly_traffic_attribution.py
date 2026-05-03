#!/usr/bin/env python3
"""Count how much traffic Jungle received from studyly.io (any signal).

Looks at MULTIPLE attribution fields, not just event_properties.utm_source:
  - event_properties.utm_source
  - event_properties.referring_domain
  - event_properties.referrer
  - user_properties.initial_utm_source
  - user_properties.initial_referring_domain
  - user_properties.initial_referrer
"""
import base64
import gzip
import io
import json
import os
import sys
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict, Counter
from datetime import datetime

EXPORT_API = "https://amplitude.com/api/2/export"
START = "20260430T01"
END   = "20260503T05"


def fetch_export(api_key, secret_key, start, end):
    auth = base64.b64encode(f"{api_key}:{secret_key}".encode()).decode()
    qs = urllib.parse.urlencode({"start": start, "end": end})
    req = urllib.request.Request(
        f"{EXPORT_API}?{qs}",
        headers={"Authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def iter_events(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if not name.endswith(".json.gz"):
                continue
            with z.open(name) as f:
                raw = gzip.decompress(f.read())
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def is_studyly(value):
    if not isinstance(value, str):
        return False
    v = value.lower()
    return "studyly.io" in v or v.startswith("studyly")


def main():
    api_key = os.environ.get("AMPLITUDE_STUDYLY_API_KEY")
    secret_key = os.environ.get("AMPLITUDE_STUDYLY_SECRET_KEY")
    if not api_key or not secret_key:
        sys.exit("missing AMPLITUDE_STUDYLY_API_KEY / SECRET_KEY env")

    print(f"# downloading export window {START}..{END} UTC", file=sys.stderr)
    blob = fetch_export(api_key, secret_key, START, END)
    print(f"# got {len(blob)/1e6:.1f} MB", file=sys.stderr)

    events = list(iter_events(blob))
    print(f"# total events in window: {len(events):,}", file=sys.stderr)

    # Buckets by attribution source
    buckets = defaultdict(lambda: defaultdict(set))  # field -> value -> set(amp_id)
    field_event_counts = defaultdict(Counter)         # field -> value -> count
    studyly_amp_ids = set()
    studyly_event_types = Counter()
    studyly_emails = set()

    fields_event = ["utm_source", "utm_medium", "utm_campaign", "referring_domain", "referrer"]
    fields_user = ["initial_utm_source", "initial_utm_medium", "initial_utm_campaign",
                   "initial_referring_domain", "initial_referrer", "referring_domain", "referrer"]

    for ev in events:
        ep = ev.get("event_properties") or {}
        up = ev.get("user_properties") or {}
        amp = ev.get("amplitude_id")
        et = ev.get("event_type")

        # Look for any studyly.io signal
        matched_field = None
        matched_value = None
        for f in fields_event:
            v = ep.get(f)
            if is_studyly(v):
                matched_field = f"event.{f}"
                matched_value = v
                break
        if not matched_field:
            for f in fields_user:
                v = up.get(f)
                if is_studyly(v):
                    matched_field = f"user.{f}"
                    matched_value = v
                    break

        if matched_field:
            buckets[matched_field][matched_value].add(amp)
            field_event_counts[matched_field][matched_value] += 1
            studyly_amp_ids.add(amp)
            studyly_event_types[et] += 1

            em = up.get("email") or up.get("$email")
            if isinstance(em, str):
                studyly_emails.add(em.lower())

    print()
    print("=" * 78)
    print("ANY EVENT REFERENCING studyly.io IN ANY ATTRIBUTION FIELD")
    print("=" * 78)
    print(f"\nDistinct amplitude_ids (visitors): {len(studyly_amp_ids)}")
    print(f"Distinct emails captured: {len(studyly_emails)}")
    print(f"Total events: {sum(studyly_event_types.values())}")

    print(f"\nBy attribution FIELD where studyly.io was found:")
    print(f"{'field':<40} {'value':<30} visitors  events")
    for field in sorted(buckets):
        for value in sorted(buckets[field]):
            n_visitors = len([a for a in buckets[field][value] if a is not None])
            n_events = field_event_counts[field][value]
            print(f"  {field:<38} {value:<30} {n_visitors:<8}  {n_events}")

    print(f"\nEvent types seen for studyly.io traffic:")
    for et, n in studyly_event_types.most_common():
        print(f"  {n:>6} {et}")

    print(f"\nEmails (if any):")
    for e in sorted(studyly_emails):
        print(f"  - {e}")


if __name__ == "__main__":
    main()
