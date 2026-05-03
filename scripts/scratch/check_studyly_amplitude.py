#!/usr/bin/env python3
"""One-shot: download last N days of studyly Amplitude export, search for a list of emails."""
import base64, gzip, io, json, os, sys, urllib.request, urllib.parse, urllib.error, zipfile
from datetime import datetime, timedelta, timezone

EMAILS = [
    "eslammansour272@gmail.com",
    "ikeduosaremen@gmail.com",
    "95trevorthompson@gmail.com",
    "test-claude-2026050116@example.com",
    "claude-qa-2026050116@example.com",
    "erepamotimothyjames@gmail.com",
]

DAYS = int(os.environ.get("DAYS", "4"))

def load_env(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env(os.path.expanduser("~/social-autoposter/.env"))
api = os.environ["AMPLITUDE_STUDYLY_API_KEY"]
sec = os.environ["AMPLITUDE_STUDYLY_SECRET_KEY"]
auth = base64.b64encode(f"{api}:{sec}".encode()).decode()

end_dt = datetime.now(timezone.utc)
start_dt = end_dt - timedelta(days=DAYS)
qs = urllib.parse.urlencode({"start": start_dt.strftime("%Y%m%dT%H"), "end": end_dt.strftime("%Y%m%dT%H")})
url = f"https://amplitude.com/api/2/export?{qs}"
print(f"# fetching {start_dt.isoformat()} -> {end_dt.isoformat()}", file=sys.stderr)

req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
try:
    with urllib.request.urlopen(req, timeout=600) as r:
        zip_bytes = r.read()
except urllib.error.HTTPError as e:
    sys.exit(f"export api {e.code}: {e.read().decode()[:400]}")
print(f"# {len(zip_bytes)/1024/1024:.1f} MB downloaded", file=sys.stderr)

emails_lc = [e.lower() for e in EMAILS]
hits = {e: [] for e in emails_lc}
total_events = 0

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
                    ev = json.loads(line)
                except Exception:
                    continue
                total_events += 1
                up = ev.get("user_properties") or {}
                ev_email = (up.get("email") or up.get("$email") or up.get("Email") or "").lower()
                ev_uid = (ev.get("user_id") or "").lower()
                for e in emails_lc:
                    if ev_email == e or ev_uid == e:
                        hits[e].append(ev)
                        break

print(f"# scanned {total_events:,} events\n")
for e in emails_lc:
    matches = hits[e]
    if not matches:
        print(f"NOT FOUND  {e}")
        continue
    matches.sort(key=lambda x: x.get("event_time") or "")
    sigs = [m for m in matches if m.get("event_type") == "New User Sign Up"]
    types = sorted({m.get("event_type") for m in matches})
    first = matches[0]
    up = first.get("user_properties") or {}
    ep = first.get("event_properties") or {}
    print(f"FOUND      {e}")
    print(f"  events: {len(matches)}  signup_events: {len(sigs)}  event_types: {types}")
    print(f"  first_seen: {first.get('event_time')}  user_id: {first.get('user_id')}  amp_id: {first.get('amplitude_id')}")
    print(f"  utm: source={ep.get('utm_source')} medium={ep.get('utm_medium')} campaign={ep.get('utm_campaign')}")
    print()
