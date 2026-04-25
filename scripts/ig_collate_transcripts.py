#!/usr/bin/env python3
"""Collate every deepgram transcript + IG caption for one creator into a single
markdown corpus. Sorted by upload_date desc."""
import json, sys, glob, os, datetime as dt

handle = sys.argv[1] if len(sys.argv) > 1 else "that.girljen_"
root = f"/Users/matthewdi/social-autoposter/scripts/ig_creators_run/{handle}"
out_path = f"{root}/corpus.md"

posts = []
for info_path in sorted(glob.glob(f"{root}/*.info.json")):
    short = os.path.basename(info_path).replace(".info.json", "")
    dgm_path = f"{root}/{short}.deepgram.json"
    if not os.path.exists(dgm_path):
        continue
    info = json.load(open(info_path))
    dgm = json.load(open(dgm_path))
    ch = dgm.get("results", {}).get("channels", [{}])[0]
    transcript = (ch.get("alternatives", [{}])[0].get("transcript") or "").strip()
    caption = (info.get("description") or "").strip()
    upload = info.get("upload_date") or ""
    upload_iso = f"{upload[:4]}-{upload[4:6]}-{upload[6:]}" if len(upload) == 8 else upload
    duration = dgm.get("metadata", {}).get("duration", 0)
    likes = info.get("like_count")
    views = info.get("view_count") or info.get("playback_count")
    comments = info.get("comment_count")
    url = info.get("webpage_url") or info.get("original_url") or f"https://www.instagram.com/reel/{short}/"
    posts.append({
        "short": short,
        "upload": upload_iso,
        "url": url,
        "duration": duration,
        "likes": likes,
        "views": views,
        "comments": comments,
        "caption": caption,
        "transcript": transcript,
    })

posts.sort(key=lambda p: p["upload"], reverse=True)

lines = [f"# @{handle} corpus", "", f"_{len(posts)} posts, sorted newest -> oldest_", ""]
for p in posts:
    lines += [
        f"## {p['upload']} | {p['short']} | {p['duration']:.0f}s",
        f"url: {p['url']}",
        f"likes={p['likes']} views={p['views']} comments={p['comments']}",
        "",
        "### caption",
        p["caption"] or "(none)",
        "",
        "### transcript",
        p["transcript"] or "(no speech)",
        "",
        "---",
        "",
    ]

with open(out_path, "w") as f:
    f.write("\n".join(lines))

print(f"wrote {out_path} ({len(posts)} posts, {sum(len(p['transcript']) for p in posts)} chars transcript, {sum(len(p['caption']) for p in posts)} chars caption)")
