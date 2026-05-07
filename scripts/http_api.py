#!/usr/bin/env python3
"""Shared HTTP helper for writing to s4l.ai API endpoints."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _base_url():
    return os.environ.get("AUTOPOSTER_API_BASE", "https://s4l.ai").rstrip("/")


def _headers():
    from identity import get_identity_header
    return {
        "Content-Type": "application/json",
        "X-Installation": get_identity_header(),
    }


def api_patch(path: str, body: dict) -> dict:
    """PATCH body to s4l.ai API. Returns parsed JSON. Raises SystemExit after retries."""
    url = f"{_base_url()}{path}"
    data = json.dumps(body).encode()
    delays = [1, 3, 9]
    last_err = None
    for attempt, delay in enumerate(delays, 1):
        try:
            req = urllib.request.Request(url, data=data, headers=_headers(), method="PATCH")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode(errors="replace")
            if 400 <= e.code < 500:
                raise SystemExit(f"[http_api] PATCH {path} HTTP {e.code}: {body_txt}")
            last_err = e
            print(f"[http_api] PATCH {path} HTTP {e.code} attempt {attempt}: {body_txt[:120]}", file=sys.stderr)
        except Exception as e:
            last_err = e
            print(f"[http_api] PATCH {path} attempt {attempt}: {e}", file=sys.stderr)
        if attempt < len(delays):
            time.sleep(delay)
    raise SystemExit(f"[http_api] PATCH {path} failed after {len(delays)} attempts: {last_err}")


def api_post(path: str, body: dict, ok_on_conflict: bool = False):
    """POST body to s4l.ai API.

    Returns parsed JSON on success.
    Returns parsed 409 body (with error key) when ok_on_conflict=True.
    Raises SystemExit on 4xx (except 409 with ok_on_conflict) or exhausted retries.
    """
    url = f"{_base_url()}{path}"
    data = json.dumps(body).encode()
    delays = [1, 3, 9]
    last_err = None
    for attempt, delay in enumerate(delays, 1):
        try:
            req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode(errors="replace")
            if e.code == 409 and ok_on_conflict:
                try:
                    return json.loads(body_txt)
                except Exception:
                    return {"error": "conflict"}
            if 400 <= e.code < 500:
                raise SystemExit(f"[http_api] POST {path} HTTP {e.code}: {body_txt}")
            last_err = e
            print(f"[http_api] POST {path} HTTP {e.code} attempt {attempt}: {body_txt[:120]}", file=sys.stderr)
        except Exception as e:
            last_err = e
            print(f"[http_api] POST {path} attempt {attempt}: {e}", file=sys.stderr)
        if attempt < len(delays):
            time.sleep(delay)
    raise SystemExit(f"[http_api] POST {path} failed after {len(delays)} attempts: {last_err}")
