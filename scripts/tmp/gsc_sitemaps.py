#!/usr/bin/env python3
"""Pull sitemap submission data per property."""
import json, os
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.expanduser("~/social-autoposter")
SA_PATH = os.path.join(ROOT, "seo/credentials/seo-autopilot-sa.json")
CONFIG_FILE = os.path.join(ROOT, "config.json")

from google.oauth2 import service_account
from googleapiclient.discovery import build

def get_service():
    creds = service_account.Credentials.from_service_account_file(
        SA_PATH, scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
    )
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)

def sitemap_for(name, prop):
    out = {"name": name, "property": prop, "sitemaps": [], "totals": {"submitted": 0, "warnings": 0, "errors": 0}}
    try:
        svc = get_service()
        resp = svc.sitemaps().list(siteUrl=prop).execute()
        for sm in resp.get("sitemap", []):
            entry = {
                "path": sm.get("path"),
                "lastSubmitted": sm.get("lastSubmitted"),
                "isPending": sm.get("isPending"),
                "type": sm.get("type"),
                "warnings": int(sm.get("warnings", 0) or 0),
                "errors": int(sm.get("errors", 0) or 0),
            }
            # contents.submitted/indexed
            for c in sm.get("contents", []):
                if c.get("type") == "web":
                    entry["submitted"] = int(c.get("submitted", 0) or 0)
                    entry["indexed"] = int(c.get("indexed", 0) or 0)
            out["sitemaps"].append(entry)
            out["totals"]["submitted"] += entry.get("submitted", 0)
            out["totals"]["warnings"] += entry["warnings"]
            out["totals"]["errors"] += entry["errors"]
    except Exception as e:
        out["error"] = str(e)[:200]
    return out

def main():
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    projects = [(p["name"], p["landing_pages"]["gsc_property"]) for p in cfg["projects"] if p.get("landing_pages", {}).get("gsc_property")]
    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(sitemap_for, n, p): n for n, p in projects}
        for fut in as_completed(futures):
            results.append(fut.result())
    out_path = os.path.join(ROOT, "scripts/tmp/gsc_sitemaps.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"wrote {out_path}")

if __name__ == "__main__":
    main()
