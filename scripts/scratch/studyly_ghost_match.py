#!/usr/bin/env python3
"""For each Amplitude amp_id that touched studyly.io, dump time + IP + UA + country
so we can correlate against our Neon signups table.
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
    return isinstance(value, str) and "studyly" in value.lower()


def main():
    api_key = os.environ.get("AMPLITUDE_STUDYLY_API_KEY")
    secret_key = os.environ.get("AMPLITUDE_STUDYLY_SECRET_KEY")
    if not api_key or not secret_key:
        sys.exit("missing creds")

    print(f"# downloading export {START}..{END}", file=sys.stderr)
    blob = fetch_export(api_key, secret_key, START, END)
    print(f"# got {len(blob)/1e6:.1f} MB", file=sys.stderr)

    events = list(iter_events(blob))

    # Find amp_ids that touch studyly anywhere
    studyly_amp_ids = set()
    for ev in events:
        ep = ev.get("event_properties") or {}
        up = ev.get("user_properties") or {}
        for f in ("utm_source", "utm_medium", "utm_campaign", "referring_domain", "referrer"):
            if is_studyly(ep.get(f)):
                studyly_amp_ids.add(ev.get("amplitude_id"))
                break
        for f in ("initial_utm_source", "initial_referring_domain", "initial_referrer", "referring_domain", "referrer"):
            if is_studyly(up.get(f)):
                studyly_amp_ids.add(ev.get("amplitude_id"))
                break

    print(f"# studyly-touched amp_ids: {len(studyly_amp_ids)}", file=sys.stderr)

    # Now collect ALL events for those amp_ids (full history in window)
    by_amp = defaultdict(list)
    for ev in events:
        if ev.get("amplitude_id") in studyly_amp_ids:
            by_amp[ev.get("amplitude_id")].append(ev)

    print()
    for amp, evs in by_amp.items():
        evs.sort(key=lambda x: x.get("event_time") or "")
        first = evs[0]
        last = evs[-1]
        ips = sorted({(e.get("ip_address") or "") for e in evs if e.get("ip_address")})
        countries = sorted({(e.get("country") or "") for e in evs if e.get("country")})
        cities = sorted({(e.get("city") or "") for e in evs if e.get("city")})
        oses = sorted({(e.get("os_name") or "") for e in evs if e.get("os_name")})
        device_ids = sorted({(e.get("device_id") or "") for e in evs if e.get("device_id")})[:3]
        emails = sorted({((e.get("user_properties") or {}).get("email") or "") for e in evs if (e.get("user_properties") or {}).get("email")})
        # initial referrer / utm
        first_ep = first.get("event_properties") or {}
        first_up = first.get("user_properties") or {}
        init_utm = first_up.get("initial_utm_source") or first_ep.get("utm_source")
        init_ref = first_up.get("initial_referrer") or first_ep.get("referrer")
        print(f"=== amp_id={amp} ===")
        print(f"  first_seen: {first.get('event_time')}  type={first.get('event_type')}")
        print(f"  last_seen:  {last.get('event_time')}   type={last.get('event_type')}")
        print(f"  events: {len(evs)}")
        print(f"  IPs: {ips}")
        print(f"  countries: {countries}  cities: {cities}")
        print(f"  os: {oses}")
        print(f"  device_ids (sample): {device_ids}")
        print(f"  emails captured: {emails or '(none)'}")
        print(f"  initial_utm_source: {init_utm}")
        print(f"  initial_referrer:   {init_ref}")
        print()

if __name__ == "__main__":
    main()
