#!/usr/bin/env python3
"""
Daily SEO pipeline report. Queries Postgres for the last 24h of activity
and sends a summary email via Gmail API.

Usage:
    python3 daily_report.py              # send email
    python3 daily_report.py --dry-run    # print to stdout, no email
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Gmail API client
GMAIL_DIR = Path.home() / "gmail-api"
sys.path.insert(0, str(GMAIL_DIR))

SCRIPT_DIR = Path(__file__).parent
ENV_PATH = SCRIPT_DIR.parent / ".env"
TO_EMAIL = "i@m13v.com"
TOKEN_PATH = GMAIL_DIR / "token_i_at_m13v.com.json"
CREDENTIALS_PATH = GMAIL_DIR / "credentials.json"


def get_db_url() -> str:
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("DATABASE_URL not found in .env")


def query_report(conn) -> dict:
    cur = conn.cursor()
    now_utc = datetime.now(timezone.utc)
    since = now_utc - timedelta(hours=24)

    # Pages created in last 24h
    cur.execute("""
        SELECT product, keyword, slug, page_url, completed_at
        FROM seo_keywords
        WHERE status = 'done' AND page_url IS NOT NULL
          AND completed_at >= %s
        ORDER BY completed_at DESC
    """, (since,))
    pages_created = cur.fetchall()

    # Keywords scored in last 24h (approximated by updated_at or completed_at)
    cur.execute("""
        SELECT product, COUNT(*) FILTER (WHERE status = 'skip') as skipped,
               COUNT(*) FILTER (WHERE status = 'done' AND completed_at >= %s) as generated,
               COUNT(*) FILTER (WHERE status = 'pending') as pending
        FROM seo_keywords
        WHERE product IN ('Assrt', 'Cyrano', 'Fazm', 'PieLine')
        GROUP BY product ORDER BY product
    """, (since,))
    product_stats = cur.fetchall()

    # Overall pool status
    cur.execute("""
        SELECT product, status, COUNT(*)
        FROM seo_keywords
        GROUP BY product, status
        ORDER BY product, status
    """)
    pool_status = cur.fetchall()

    # Errors in last 24h (keywords stuck in scoring/in_progress)
    cur.execute("""
        SELECT product, keyword, status
        FROM seo_keywords
        WHERE status IN ('scoring', 'in_progress')
        ORDER BY product
    """)
    stuck = cur.fetchall()

    cur.close()
    return {
        "pages_created": pages_created,
        "product_stats": product_stats,
        "pool_status": pool_status,
        "stuck": stuck,
        "since": since,
        "now": now_utc,
    }


def format_report(data: dict) -> tuple[str, str]:
    """Returns (subject, html_body)."""
    pages = data["pages_created"]
    since = data["since"]
    now = data["now"]
    stuck = data["stuck"]

    date_str = now.strftime("%Y-%m-%d")
    subject = f"SEO Pipeline: {len(pages)} pages created ({date_str})"

    lines = []
    lines.append(f"<h2>SEO Pipeline Daily Report</h2>")
    lines.append(f"<p><strong>Period:</strong> {since.strftime('%Y-%m-%d %H:%M')} to {now.strftime('%Y-%m-%d %H:%M')} UTC</p>")

    # Pages created
    lines.append(f"<h3>{len(pages)} Pages Created</h3>")
    if pages:
        lines.append("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:14px'>")
        lines.append("<tr><th>Product</th><th>Keyword</th><th>URL</th><th>Time</th></tr>")
        for product, keyword, slug, url, completed_at in pages:
            time_str = completed_at.strftime("%H:%M") if completed_at else "?"
            lines.append(f"<tr><td>{product}</td><td>{keyword}</td>"
                         f"<td><a href='{url}'>{slug}</a></td><td>{time_str}</td></tr>")
        lines.append("</table>")

        # Per-product summary
        from collections import Counter
        product_counts = Counter(p[0] for p in pages)
        lines.append("<p><strong>By product:</strong> " +
                     ", ".join(f"{p}: {c}" for p, c in sorted(product_counts.items())) +
                     "</p>")
    else:
        lines.append("<p>No pages created in this period.</p>")

    # Keyword pool status
    lines.append("<h3>Keyword Pool</h3>")
    lines.append("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:14px'>")
    lines.append("<tr><th>Product</th><th>Done</th><th>Skip</th><th>Unscored</th><th>Scoring</th><th>Pending</th><th>In Progress</th></tr>")

    pool = {}
    for product, status, count in data["pool_status"]:
        if product not in pool:
            pool[product] = {}
        pool[product][status] = count

    for product in sorted(pool.keys()):
        s = pool[product]
        unscored = s.get("unscored", 0)
        warning = " ⚠️" if unscored < 20 else ""
        lines.append(f"<tr><td>{product}</td>"
                     f"<td>{s.get('done', 0)}</td>"
                     f"<td>{s.get('skip', 0)}</td>"
                     f"<td>{unscored}{warning}</td>"
                     f"<td>{s.get('scoring', 0)}</td>"
                     f"<td>{s.get('pending', 0)}</td>"
                     f"<td>{s.get('in_progress', 0)}</td></tr>")
    lines.append("</table>")

    # Stuck keywords warning
    if stuck:
        lines.append(f"<h3>Stuck Keywords ({len(stuck)})</h3>")
        lines.append("<p style='color:red'>These keywords are stuck in scoring/in_progress and may need manual intervention:</p>")
        lines.append("<ul>")
        for product, keyword, status in stuck:
            lines.append(f"<li>[{product}] {keyword} ({status})</li>")
        lines.append("</ul>")

    lines.append("<hr><p style='color:gray;font-size:12px'>Sent by social-autoposter/seo/daily_report.py</p>")

    return subject, "\n".join(lines)


def send_email(subject: str, html_body: str) -> None:
    from gmail_client import GmailClient

    client = GmailClient(
        credentials_path=str(CREDENTIALS_PATH),
        token_path=str(TOKEN_PATH),
    )
    client.authenticate()
    client.send_message(to=TO_EMAIL, subject=subject, body=html_body, html=True)
    print(f"Email sent to {TO_EMAIL}: {subject}")


def main():
    import psycopg2

    dry_run = "--dry-run" in sys.argv

    conn = psycopg2.connect(get_db_url())
    data = query_report(conn)
    conn.close()

    subject, html_body = format_report(data)

    if dry_run:
        print(f"Subject: {subject}")
        print()
        # Strip HTML for terminal readability
        import re
        text = re.sub(r"<[^>]+>", " ", html_body)
        text = re.sub(r"\s+", " ", text).strip()
        print(text)
    else:
        send_email(subject, html_body)


if __name__ == "__main__":
    main()
