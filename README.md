# social-autoposter

Automated social posting pipeline for Reddit, X/Twitter, LinkedIn, and Moltbook. Runs as a Claude Code skill with launchd scheduling. Also exposes an **MCP server** so any AI agent can connect to it.

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

## MCP Server (connect any AI agent)

The MCP server exposes the autoposter as tools that any MCP-compatible agent can call — Claude Desktop, custom agents, other desktop AI products.

### Install

```bash
git clone https://github.com/m13v/social-autoposter ~/social-autoposter
cd ~/social-autoposter
uv venv --python 3.12 .venv
uv pip install -e .
```

### Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "social-autoposter": {
      "command": "/Users/YOU/social-autoposter/.venv/bin/social-autoposter"
    }
  }
}
```

### Connect to any MCP client

```bash
# Run the server over stdio (default MCP transport)
.venv/bin/social-autoposter
```

### Available tools

| Tool | Description |
|------|-------------|
| `get_posts` | Query posts with filters (platform, status, date range) |
| `get_stats` | Engagement summary by platform — totals, averages, top posts |
| `get_replies` | Query replies to our posts (pending, replied, skipped) |
| `search_posts` | Full-text search across post content and thread titles |
| `get_post_history` | Full details for a post including all reply threads |
| `log_post` | Record a new post to the database |
| `check_rate_limit` | Check if posting is within rate limits |
| `update_post_status` | Update status or engagement metrics for a post |

### Available resources

| Resource URI | Description |
|--------------|-------------|
| `social://schema` | Database schema |
| `social://config` | Current config (secrets redacted) |
| `social://content-rules` | Content style guide for posting |

### Import as a Python library

```python
from social_autoposter.db import get_connection, rows_to_dicts

conn = get_connection()
posts = conn.execute("SELECT * FROM posts WHERE platform='reddit' ORDER BY upvotes DESC LIMIT 5").fetchall()
for post in rows_to_dicts(posts):
    print(f"{post['upvotes']} upvotes: {post['our_content'][:80]}")
```

## Structure

```
social-autoposter/
├── social_posts.db          <- SQLite database (committed, browsable via Datasette Lite)
├── schema.sql               <- DB schema for reproducibility
├── pyproject.toml           <- Python package definition (MCP server)
├── social_autoposter/       <- Python package
│   ├── server.py            <- MCP server with tools + resources
│   └── db.py                <- Database helpers
├── setup.sh                 <- creates symlinks, loads launchd agents
├── skill/
│   ├── SKILL.md             <- skill documentation + content rules
│   ├── run.sh               <- hourly posting script
│   ├── stats.sh             <- 6-hourly stats updater
│   ├── engage.sh            <- 2-hourly reply engagement
│   ├── candidates.md        <- historical candidate log
│   └── logs/                <- gitignored runtime logs
└── launchd/
    ├── com.m13v.social-autoposter.plist
    ├── com.m13v.social-stats.plist
    └── com.m13v.social-engage.plist
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
