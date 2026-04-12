# social-autoposter

Automated social posting pipeline for Reddit, X/Twitter, LinkedIn, and Moltbook. Ships as a Claude Code skill plus a set of standalone Python helpers and macOS launchd jobs.

Posts are written to a shared Neon Postgres database via `DATABASE_URL` in `~/social-autoposter/.env`. Each platform drives its own persistent Playwright MCP browser profile, so logins survive across runs.

## Prerequisites

A new machine needs all of these before the pipeline can run end to end:

- **macOS** (the launchd plists are mac-only; Linux users can crib the cron snippets from `setup/SKILL.md` Step 7)
- **Node.js 16+** (for `npx`, the installer, and `@playwright/mcp` at runtime)
- **Python 3.9+** with `pip3` (helper scripts; `psycopg2-binary` is auto-installed by the installer)
- **Claude Code CLI** on `PATH` (the cron scripts shell out to `claude -p` with a per-platform MCP config)
- **`psql`** on `PATH` (a few scripts query Neon directly)
- One Chromium install per platform (created on first run by `@playwright/mcp` against the persistent profile dirs)

Optional:

- `MOLTBOOK_API_KEY` in `.env` for Moltbook posting and scanning
- `RESEND_API_KEY` and `NOTIFICATION_EMAIL` in `.env` for DM-escalation emails

## Install

```bash
npx social-autoposter init
```

`bin/cli.js` does all of the wiring in one shot:

1. Copies `scripts/`, `skill/`, `setup/`, `SKILL.md`, `schema-postgres.sql`, and `browser-agent-configs/` into `~/social-autoposter/`
2. Creates `config.json` from `config.example.json` and `.env` from `.env.example` (the shared Neon `DATABASE_URL` is pre-filled)
3. Installs `psycopg2-binary` via `pip3` if missing
4. Generates launchd plists in `~/social-autoposter/launchd/` with the user's actual `HOME` and `PATH`
5. Installs the Playwright MCP configs to `~/.claude/browser-agent-configs/` (twitter, reddit, linkedin) with `__HOME__` and `__NODE_BIN__` placeholders substituted. Existing files are left alone, so any window-position tweaks survive `npx social-autoposter update`.
6. Creates empty persistent browser profile dirs at `~/.claude/browser-profiles/{twitter,reddit,linkedin}`
7. Symlinks `~/.claude/skills/social-autoposter` and `~/.claude/skills/social-autoposter-setup` to the install dir

To refresh code without touching user files (`config.json`, `.env`, `SKILL.md`, or any browser config you customized):

```bash
npx social-autoposter update
```

## Configure

Tell your Claude Code agent: **"set up social autoposter"**. The interactive wizard in `setup/SKILL.md` walks through:

1. Verifying the Neon connection
2. Filling in `~/social-autoposter/config.json` with handles for Reddit, Twitter, LinkedIn, optional Moltbook
3. A 5-question interview to draft your `content_angle`
4. Capturing `projects` with `topics` (used by the tiered reply strategy)
5. Verifying browser logins per platform via the dedicated MCP agent. The first time each platform runs you'll be asked to log in once; cookies persist into the userDataDir under `~/.claude/browser-profiles/`.
6. A dry-run of `find_threads.py --limit 3`
7. Optional: loading the launchd plists into `~/Library/LaunchAgents/`

## How the runtime is wired

```
launchd  ──▶  skill/run-{platform}.sh  ──▶  claude -p  --strict-mcp-config  --mcp-config ~/.claude/browser-agent-configs/{platform}-agent-mcp.json
                       │                                        │
                       │                                        └──▶  @playwright/mcp@latest
                       │                                                       │
                       │                                                       └──▶  ~/.claude/browser-profiles/{platform}/  (persistent userDataDir)
                       │
                       ├──▶  scripts/find_{tweets,threads}.py  (no browser, API + DB dedup)
                       ├──▶  scripts/pick_project.py            (weighted project rotation)
                       ├──▶  scripts/top_performers.py          (feedback report from past stats)
                       └──▶  Neon Postgres                      (DATABASE_URL in .env)
```

Each `skill/run-*.sh`:

1. Controlled by launchd (load/unload). Use the dashboard Pause All / Resume All button, or `launchctl unload/load` directly
2. Acquires a per-platform lock from `skill/lock.sh` (waits up to 60 min for any prior run)
3. Sources `~/social-autoposter/.env`
4. Picks a project, builds a feedback report, fetches `llms.txt` for product context
5. Calls `find_*.py` for API-side candidates already deduped against the DB
6. Spawns a child Claude process with `--strict-mcp-config` so it only sees the one platform's browser MCP

The launchd schedules from `bin/cli.js`:

| Job | Cadence |
|-----|---------|
| `com.m13v.social-autoposter` (`run.sh`) | every 3600 s (hourly) |
| `com.m13v.social-stats` (`stats.sh`) | every 21600 s (6 h) |
| `com.m13v.social-engage` (`engage.sh`) | every 21600 s (6 h) |

Per-platform plists in `launchd/` (twitter, reddit, linkedin, moltbook, github, octolens, audit, dm-replies, scan-replies, etc.) use either `StartInterval` or `StartCalendarInterval` for fixed wall-clock times. Activate them with:

```bash
ln -sf ~/social-autoposter/launchd/com.m13v.social-twitter.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.m13v.social-twitter.plist
```

## Skill commands

| Command | What it does |
|---------|-------------|
| `/social-autoposter` | Comment run: find threads, draft, post, log (cron-safe) |
| `/social-autoposter post` | Create an original post or thread (manual only) |
| `/social-autoposter stats` | Update engagement stats via API |
| `/social-autoposter engage` | Scan and reply to responses on our posts |
| `/social-autoposter audit` | Full browser audit of all posts |

View live stats at `https://s4l.ai/stats/<your-handle>` once posts start landing in Neon.

## Repo layout

```
social-autoposter/
├── SKILL.md                  the playbook (locked, immutable)
├── bin/cli.js                installer + dashboard launcher
├── browser-agent-configs/    Playwright MCP templates (twitter/reddit/linkedin)
├── config.example.json       config template
├── schema-postgres.sql       Neon schema
├── setup/SKILL.md            interactive setup wizard skill (locked)
├── scripts/                  Python and JS helpers (no browser, no LLM)
├── skill/                    shell wrappers invoked by launchd
└── launchd/                  generated macOS LaunchAgent plists
```

## For other AI agents

The skill works with any agent that has shell access, browser automation, and an LLM. The Python and JS helpers in `scripts/` handle thread discovery, reply scanning, and stats updates without needing a browser. `SKILL.md` is the playbook; any agent can read it and execute the workflows with its own tools.

## Pause and resume

```bash
touch ~/.social-paused   # halts every cron run cleanly
rm ~/.social-paused      # resume
```
