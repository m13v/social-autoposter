# Social Autoposter

## SKILL.md - Single File, No Copies

`SKILL.md` lives at the repo root. There is no `skill/SKILL.md`.

- `~/.claude/skills/social-autoposter` -> `~/social-autoposter/` (repo root)
- Claude loads `~/social-autoposter/SKILL.md` directly
- Shell scripts in `skill/` reference `$REPO_DIR/SKILL.md` (the root)
- `bin/cli.js` deletes any stale `skill/SKILL.md` during init/update
- `setup/SKILL.md` is a separate file (setup wizard, not the main skill)

### Rules

- **NEVER create `skill/SKILL.md`** - not as a file, not as a symlink
- **NEVER edit, modify, or write to `SKILL.md`** - it is locked with `chflags uchg` (macOS immutable flag). Do not attempt to unlock it. Do not run `chflags nouchg`. Do not run `chmod`. Just read it and follow the workflow exactly as written.
- **NEVER edit, modify, or write to `setup/SKILL.md`** - it is also locked with `chflags uchg`. Same rules as above: do not unlock, do not modify, do not "fix" it.
- **Do NOT add rate limits to ANY file** - do not add "Max N posts per 24 hours" or any posting rate limit language to SKILL.md, setup/SKILL.md, shell scripts, or any other file. There is NO posting rate limit. If a file does not contain a rate limit, that is intentional.
- **Do NOT "improve" or "fix" the workflow** - do not renumber steps, do not add safety checks, do not restructure the file.


## Known unresolved issue: hung runs from BSD grep on /tmp FIFOs

A `run-*.sh` can occasionally hang indefinitely because the model invokes `grep -r` across `/tmp` (or `~/`) during a session. macOS BSD `grep` opens named pipes it encounters (e.g. stale `ad_mailbox_*` FIFOs left by Apple daemons) and blocks forever in `read()`, which freezes the shell, the `claude -p` parent, and prevents launchd from re-firing the job. No automatic recovery is in place: wrapping `claude` in `timeout` was rejected, and neither FIFO sweeps nor switching to GNU grep fully eliminates the class of problem. For now, if a posting run stops making progress, kill the stuck `run-*.sh` tree manually.


## LinkedIn: flagged patterns (DO NOT REINTRODUCE)

2026-04-17 the account was restricted after a patch added Voyager-API scraping and per-permalink scroll-and-expand loops. Volume (2-3 posts/day) was NOT the cause, behavioral fingerprinting of scripted browser activity was. Banned in this repo:

- `/voyager/api/*` calls of any kind (Python, `fetch()`, `page.evaluate`). That is the internal web-client backend, not the public API.
- Loops that open each post permalink to scrape reactions/comments, or combine `scrollBy` with clicks on "Show more comments" / "Load earlier replies".
- Python Playwright/CDP helpers for LinkedIn. No programmatic `login()` flows.

Allowed: `scripts/linkedin_api.py` (OAuth `api.linkedin.com/v2/socialActions/*`, documented) for posting, and `mcp__linkedin-agent__*` (real headed Chrome) for any browser work, driven by Claude inside the shell pipelines. Session checks are passive: if login/checkpoint appears, print `SESSION_INVALID` and stop.

New LinkedIn capability? Extend `linkedin_api.py` or add a Claude-driven `mcp__linkedin-agent__` step. Do not write a new Python CDP helper.

## Engagement Styles System (DO NOT REMOVE)

All posting and engagement scripts use `scripts/engagement_styles.py` to generate a `STYLES_BLOCK` variable injected into prompts. This is an A/B testing system that tracks which comment style gets the best engagement.

- **NEVER remove `STYLES_BLOCK`** from any `skill/run-*.sh` or `skill/engage*.sh` script
- **NEVER remove `engagement_style`** from DB logging (reply_db.py calls, INSERT statements)
- **NEVER remove or simplify style definitions** in `scripts/engagement_styles.py`
- **NEVER inline style definitions** back into individual scripts; the shared module is the single source of truth
