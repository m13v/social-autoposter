# social-autoposter

Automated social posting pipeline for Reddit, X/Twitter, and LinkedIn. Runs as a Claude Code skill with launchd scheduling.

## Browse the data

[**Open in Datasette Lite**](https://lite.datasette.io/?url=https://raw.githubusercontent.com/m13v/social-autoposter/main/social_posts.db)

## How it works

```
launchd (hourly)          launchd (every 6h)
      │                         │
      ▼                         ▼
   run.sh                   stats.sh
      │                         │
      ├─ prompt-db search       ├─ Reddit JSON API
      ├─ check DB for dupes     ├─ update scores
      ├─ find thread            ├─ detect deleted
      ├─ post via Playwright    └─ git push DB
      └─ log to DB
```

- **run.sh** — Finds recent dev work via prompt-db, searches for relevant threads, posts a comment via Playwright MCP, logs to SQLite
- **stats.sh** — Fetches Reddit engagement stats via public JSON API, updates the DB, pushes to GitHub so Datasette Lite stays fresh

## Structure

```
social-autoposter/
├── social_posts.db          ← SQLite database (committed, browsable via Datasette Lite)
├── schema.sql               ← DB schema for reproducibility
├── setup.sh                 ← creates symlinks, loads launchd agents
├── skill/
│   ├── SKILL.md             ← skill documentation + content rules
│   ├── run.sh               ← hourly posting script
│   ├── stats.sh             ← 6-hourly stats updater
│   ├── candidates.md        ← historical candidate log
│   └── logs/                ← gitignored runtime logs
└── launchd/
    ├── com.m13v.social-autoposter.plist
    └── com.m13v.social-stats.plist
```

## Setup

```bash
git clone https://github.com/m13v/social-autoposter ~/social-autoposter
bash ~/social-autoposter/setup.sh
```

This creates symlinks so everything works from its original location:
- `~/.claude/skills/social-autoposter` → `~/social-autoposter/skill/`
- `~/.claude/social_posts.db` → `~/social-autoposter/social_posts.db`
- LaunchAgent plists symlinked into `~/Library/LaunchAgents/`

## Accounts

- **Reddit**: u/Deep_Ad1959
- **X/Twitter**: @m13v_
- **LinkedIn**: Matthew Diakonov
