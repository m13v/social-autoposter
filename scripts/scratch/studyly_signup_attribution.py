#!/usr/bin/env python3
"""One-shot Amplitude analysis for studyly signups.

Downloads the export ONCE for the given window, then for every email of
interest:
  - direct match by user_properties.email or user_id
  - indirect match by amplitude_id linked to a direct match
And separately surfaces:
  - any event with utm_source startswith 'studyly.io' inside the window,
    with email/user_id, so we can see if anyone signed up under a different
    email but came from our funnel.
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
from collections import defaultdict
from datetime import datetime, timezone

EXPORT_API = "https://amplitude.com/api/2/export"

EMAILS_OF_INTEREST = [
    "eslammansour272@gmail.com",
    "ikeduosaremen@gmail.com",
    "95trevorthompson@gmail.com",
    "erepamotimothyjames@gmail.com",
]

START = "20260430T01"  # UTC
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


def event_email(ev):
    up = ev.get("user_properties") or {}
    for k in ("email", "$email", "Email"):
        v = up.get(k)
        if isinstance(v, str):
            return v.lower()
    uid = ev.get("user_id") or ""
    if "@" in uid:
        return uid.lower()
    return None


def main():
    api_key = os.environ.get("AMPLITUDE_STUDYLY_API_KEY")
    secret_key = os.environ.get("AMPLITUDE_STUDYLY_SECRET_KEY")
    if not api_key or not secret_key:
        sys.exit("missing AMPLITUDE_STUDYLY_API_KEY / SECRET_KEY env")

    print(f"# downloading export window {START}..{END} UTC", file=sys.stderr)
    blob = fetch_export(api_key, secret_key, START, END)
    print(f"# got {len(blob)/1e6:.1f} MB", file=sys.stderr)

    events = list(iter_events(blob))
    print(f"# total events: {len(events)}", file=sys.stderr)

    targets = {e.lower() for e in EMAILS_OF_INTEREST}

    # Pass 1: collect amplitude_ids that have any direct hit on a target email
    amp_to_email = defaultdict(set)
    direct_hit_amp = {}  # email -> set(amp_id)
    for e in EMAILS_OF_INTEREST:
        direct_hit_amp[e.lower()] = set()

    for ev in events:
        em = event_email(ev)
        if em and em in targets:
            amp = ev.get("amplitude_id")
            direct_hit_amp[em].add(amp)
            if amp is not None:
                amp_to_email[amp].add(em)

    # Pass 2: collect events for each target email (direct + amp linked)
    per_email_events = {e: [] for e in targets}
    studyly_events = []  # everything with utm_source startswith studyly.io

    for ev in events:
        em = event_email(ev)
        amp = ev.get("amplitude_id")
        ep = ev.get("event_properties") or {}
        utm = (ep.get("utm_source") or "").lower()

        # add to email bucket
        for tgt in targets:
            if em == tgt or (amp is not None and amp in direct_hit_amp[tgt]):
                per_email_events[tgt].append(ev)
                break

        if utm.startswith("studyly.io"):
            studyly_events.append(ev)

    print()
    print("=" * 70)
    print("PER-EMAIL EVENTS IN AMPLITUDE")
    print("=" * 70)
    for tgt in EMAILS_OF_INTEREST:
        evs = per_email_events[tgt.lower()]
        print(f"\n--- {tgt} ---")
        if not evs:
            print("  NO EVENTS in window")
            continue
        for ev in sorted(evs, key=lambda x: x.get("event_time") or ""):
            ep = ev.get("event_properties") or {}
            utm = ep.get("utm_source") or ""
            print(f"  {ev.get('event_time'):<24} {ev.get('event_type'):<40}"
                  f" utm={utm} amp_id={ev.get('amplitude_id')}"
                  f" user_id={(ev.get('user_id') or '')[:30]}")

    print()
    print("=" * 70)
    print(f"ALL EVENTS WITH utm_source LIKE 'studyly.io%' (n={len(studyly_events)})")
    print("=" * 70)
    # Group by amp_id + event_type so we can see distinct sessions
    by_amp = defaultdict(list)
    for ev in studyly_events:
        by_amp[ev.get("amplitude_id")].append(ev)

    print(f"\n{len(by_amp)} distinct amplitude_ids touched studyly.io UTM in window")
    print(f"{'amp_id':<14} {'email':<38} {'first_event':<24} {'utm':<20} {'event_types'}")

    for amp, evs in sorted(by_amp.items(), key=lambda kv: min(e.get("event_time") or "" for e in kv[1])):
        evs_sorted = sorted(evs, key=lambda x: x.get("event_time") or "")
        first = evs_sorted[0]
        em = ""
        for e in evs_sorted:
            tmp = event_email(e)
            if tmp:
                em = tmp
                break
        types = sorted({(e.get("event_type") or "").strip() for e in evs_sorted})
        types_str = ", ".join(types)[:60]
        utm = (first.get("event_properties") or {}).get("utm_source") or ""
        target_marker = " <-- TARGET" if em in targets else ""
        print(f"{str(amp)[:13]:<14} {(em or '(no email)')[:37]:<38} "
              f"{(first.get('event_time') or '')[:23]:<24} {utm[:19]:<20} {types_str}{target_marker}")


if __name__ == "__main__":
    main()
