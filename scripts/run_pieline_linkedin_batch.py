#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()

import json
import os
import subprocess

import psycopg2


ITEMS = [
    {
        "activity_id": "7442963966743597056",
        "author": "Intellectum Lab",
        "title": "Case study on voice AI for restaurant order automation",
        "thread_content": "Case study about restaurant phone ordering automation, production iteration, cost per call, and completion rate.",
        "comment": "the jump from 68% to 100% only after changing the architecture is the part people gloss over. restaurant voice systems do not break on happy-path demos, they break on modifiers, background noise, and peak-hour latency. getting cost and completion rate into production shape at the same time is the real milestone.",
        "source_summary": "linkedin search: restaurant phone ordering AI automation",
    },
    {
        "activity_id": "7442429023584120832",
        "author": "Ken Haumschilt",
        "title": "Chowbus funding and AI operating system for restaurants",
        "thread_content": "Post arguing that restaurant AI is moving from tools into full operating systems for orders, calls, marketing, and operations.",
        "comment": "the execution point is the real one. restaurants are not buying an ai operating system, they are buying fewer missed calls, faster order capture, and less chaos during rushes. the products that win here will be the ones that plug into existing pos and kitchen flow without adding another screen.",
        "source_summary": "linkedin search: restaurant phone ordering AI automation",
    },
    {
        "activity_id": "7440611699126185985",
        "author": "Mugunth Subramanian",
        "title": "Automation opportunities hiding inside small restaurant operations",
        "thread_content": "Post about restaurant service bursts showing where automation can reduce operational pressure for small businesses.",
        "comment": "this is why restaurant automation usually starts with the phone and order flow, not some giant back-office rollout. one rush with overlapping calls and in-person traffic is enough to show where margin leaks out. small operators feel those interruptions immediately.",
        "source_summary": "linkedin search: restaurant phone ordering AI automation",
    },
    {
        "activity_id": "7444172890645618689",
        "author": "Tran Tran",
        "title": "Yum Brands AI factory and QSR personalization at scale",
        "thread_content": "Post about Yum Brands using AI personalization across a large restaurant footprint.",
        "comment": "the scale is impressive, but the more interesting test is whether the experience actually survives restaurant edge cases. personalization is great until a caller has modifiers, background noise, or a staff handoff in the middle of rush. the operators who win will be the ones turning ai into smoother service, not just another headline.",
        "source_summary": "linkedin search: voice AI restaurant operations technology",
    },
    {
        "activity_id": "7439306574499614720",
        "author": "Aidan Chau",
        "title": "Maple partnership with Shift4 for AI phone ordering in SkyTab POS",
        "thread_content": "Post announcing Maple and Shift4 partnership to bring AI phone ordering into the SkyTab POS ecosystem.",
        "comment": "the distribution angle here matters as much as the model quality. once voice ordering lands inside the pos ecosystem operators already trust, adoption gets way easier because staff are not reconciling two systems during service. that invisible handoff is what makes restaurant ai stick.",
        "source_summary": "linkedin search: POS integration AI phone ordering restaurant",
    },
    {
        "activity_id": "7444784562930561024",
        "author": "SUPRIYA KUMARI",
        "title": "POS technology improving efficiency in retail and hospitality",
        "thread_content": "Post about advanced POS devices and hospitality technology improving business efficiency and customer experience.",
        "comment": "hardware still shapes a lot of restaurant speed. if the pos layer is slow or fragmented, every order takes longer and staff start building workarounds. the best ai and automation pieces are the ones that disappear into that existing flow instead of asking teams to learn a whole new motion.",
        "source_summary": "linkedin search: restaurant technology operations efficiency",
    },
    {
        "activity_id": "7443365272385765377",
        "author": "Marty AI",
        "title": "Why isolated restaurant tech tools fail without operational fit",
        "thread_content": "Post arguing that restaurant tech fails when it automates one task in isolation without fitting the broader operation.",
        "comment": "this is the core lesson. restaurants rarely need one more isolated automation tool, they need coverage across the messy handoffs between calls, orders, kitchen, and pickup. if the system cannot live inside the existing operation, the labor problem just moves around.",
        "source_summary": "linkedin search: restaurant AI phone ordering POS",
    },
    {
        "activity_id": "7443367527440662529",
        "author": "RS Enterprise Group LLC",
        "title": "AI phone agent for restaurants handling concurrent calls and POS flow",
        "thread_content": "Post about AI phone agents for restaurants handling concurrent calls, deliveries, upsells, regulars, and payments.",
        "comment": "the concurrent-calls point is the one that gets underestimated. a lot of restaurants do fine when one person is ringing, then the whole system breaks the moment three calls hit during service. if the agent can actually handle modifiers, repeat customers, and pos handoff cleanly, that is where the roi shows up.",
        "source_summary": "linkedin search: restaurant AI phone ordering POS",
    },
    {
        "activity_id": "7440135789683859456",
        "author": "T-Menu",
        "title": "How small restaurants use AI to compete with chains",
        "thread_content": "Post about independent restaurants closing the technology gap with chains through lower-cost AI tools and integrated operations.",
        "comment": "this is exactly why chain-level tech advantages used to feel impossible for independents to close. the gap was never just digital menus, it was having orders, phones, and kitchen flow tied together tightly enough that staff were not juggling everything manually. lower-cost ai tooling is finally making that stack realistic for smaller operators too.",
        "source_summary": "linkedin search: POS integration AI phone ordering restaurant",
    },
    {
        "activity_id": "7439397033322418176",
        "author": "Asteris AI",
        "title": "Shift4 adding AI phone ordering to SkyTab POS",
        "thread_content": "Post about Shift4 integrating AI phone ordering into SkyTab POS with menu data, pricing, and modifiers from the POS.",
        "comment": "pulling menu data and modifiers from the pos is the difference between a demo and a usable phone ordering system. operators care about one thing here: does the order land correctly without extra cleanup during rush. if that part works, the value is obvious fast.",
        "source_summary": "linkedin search: POS integration AI phone ordering restaurant",
    },
]


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    success = []
    failed = []

    for item in ITEMS:
        cur.execute(
            "SELECT id FROM posts WHERE platform='linkedin' AND (thread_url LIKE %s OR our_url LIKE %s) LIMIT 1",
            (f"%{item['activity_id']}%", f"%{item['activity_id']}%"),
        )
        row = cur.fetchone()
        if row:
            failed.append([item["activity_id"], f"already in DB as {row[0]}"])
            continue

        proc = subprocess.run(
            ["python3", "scripts/linkedin_api.py", "comment", item["activity_id"], item["comment"]],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            failed.append([item["activity_id"], proc.stderr.strip() or proc.stdout.strip()])
            continue

        try:
            resp = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            failed.append([item["activity_id"], f"bad json: {proc.stdout.strip()}"])
            continue

        if not resp.get("ok"):
            failed.append([item["activity_id"], resp])
            continue

        our_url = resp["our_url"]
        cur.execute(
            """
            INSERT INTO posts (
              platform, thread_url, thread_author, thread_title, thread_content,
              our_url, our_content, our_account, source_summary, project_name,
              status, posted_at, feedback_report_used
            ) VALUES (
              'linkedin', %s, %s, %s, %s,
              %s, %s, %s, %s, 'PieLine',
              'active', NOW(), TRUE
            ) RETURNING id
            """,
            (
                our_url,
                item["author"],
                item["title"],
                item["thread_content"],
                our_url,
                item["comment"],
                "Matthew Diakonov",
                item["source_summary"],
            ),
        )
        post_id = cur.fetchone()[0]
        conn.commit()
        success.append([post_id, item["activity_id"], item["author"]])

    cur.close()
    conn.close()
    print(json.dumps({"success": success, "failed": failed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
