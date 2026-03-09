# social-autoposter

Automated social posting pipeline for Reddit, X/Twitter, LinkedIn, and Moltbook. Install as an AI agent skill or use the standalone Python scripts.

[**Browse the data in Datasette Lite**](https://lite.datasette.io/?url=https://raw.githubusercontent.com/m13v/social-autoposter/main/social_posts.db)

## Install as a skill

```bash
npx social-autoposter init
```

Then tell your agent: **"set up social autoposter"** — the setup skill walks you through config, DB creation, browser logins, and a test run.

To update scripts without touching your config or database:
```bash
npx social-autoposter update
```

Or set up manually:
```bash
cp config.example.json config.json   # edit with your accounts
sqlite3 social_posts.db < schema.sql # create the database
bash setup.sh                        # symlinks + launchd (macOS)
```

## How it works

```
SKILL.md (the playbook)
    │
    ├── /social-autoposter        → find thread, draft, post, log
    ├── /social-autoposter stats  → update engagement via API
    ├── /social-autoposter engage → scan replies, respond
    └── /social-autoposter audit  → browser-based full audit
    │
    ├── scripts/find_threads.py   → thread discovery (no browser)
    ├── scripts/scan_replies.py   → reply scanning (no browser)
    └── scripts/update_stats.py   → stats fetching (no browser)
    │
    ├── skill/run.sh              → launchd wrapper (hourly)
    ├── skill/stats.sh            → launchd wrapper (6-hourly)
    └── skill/engage.sh           → launchd wrapper (2-hourly)
```

## Structure

```
social-autoposter/
├── SKILL.md               <- skill playbook (generic, publishable)
├── config.example.json    <- config template (accounts, subreddits, content angle)
├── schema.sql             <- DB schema
├── setup.sh               <- creates symlinks, loads launchd agents
├── setup/
│   └── SKILL.md           <- interactive setup wizard skill
├── scripts/
│   ├── find_threads.py    <- find candidate threads via Reddit/Moltbook API
│   ├── scan_replies.py    <- scan for new replies to our posts via API
│   └── update_stats.py    <- fetch engagement stats via API
├── skill/
│   ├── SKILL.md           <- personal skill (hardcoded accounts)
│   ├── run.sh             <- hourly posting (launchd wrapper)
│   ├── stats.sh           <- 6-hourly stats (launchd wrapper)
│   ├── engage.sh          <- 2-hourly engagement (launchd wrapper)
│   └── logs/              <- runtime logs (gitignored)
├── social_posts.db        <- SQLite database (committed for Datasette)
├── syncfield.sh           <- sync SQLite -> Neon Postgres
└── launchd/               <- macOS LaunchAgent plists
```

## For other AI agents

The skill is designed to work with any agent that has:
- **Shell access** (to run Python scripts and sqlite3)
- **Browser automation** (Playwright, Selenium, etc. for posting)
- **An LLM** (for drafting comments in the right tone)

The Python scripts handle thread discovery, reply scanning, and stats updates without needing a browser or LLM. The SKILL.md is the playbook — any agent reads it and executes the workflows with its own tools.

## Accounts

- **Reddit**: u/Deep_Ad1959
- **X/Twitter**: @m13v_
- **LinkedIn**: Matthew Diakonov
- **Moltbook**: matthew-autoposter
