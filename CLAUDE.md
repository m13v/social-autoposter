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


## LinkedIn: flagged patterns to avoid (DO NOT REINTRODUCE)

On 2026-04-17 the LinkedIn account was temporarily restricted after a patch added bulk comment scraping. Root cause was behavioral fingerprinting of scripted browser activity, not comment volume (we only post 2-3 times/day). The restriction was traced to commit `26845ed` which added per-permalink scroll-and-expand loops minutes before the first flagged run. Everything below is banned in this repo unless the user explicitly asks for it.

**Endpoints / request shapes that LinkedIn flags:**
- `https://www.linkedin.com/voyager/api/*` (internal Voyager GraphQL/REST). This is NOT the documented API, it is the web client's private backend. Calling it with `fetch()` from the logged-in session trips behavioral signals. Use `scripts/linkedin_api.py` (OAuth `api.linkedin.com/v2/socialActions/*`) for posting; it is the documented, authorized integration.
- Any `page.evaluate(async () => fetch('/voyager/api/...'))` inside Playwright/CDP.

**Browser patterns that LinkedIn flags:**
- Opening each post permalink in sequence to scrape reactions/comments (bulk audit loops).
- `window.scrollBy(0, N)` loops combined with clicking every visible "Show more comments" / "Load earlier replies" button to expand comment threads.
- Running a Python-driven Playwright/CDP script that logs in headlessly or programmatically (`login()` functions that type email/password, handle captchas, or detect and bypass challenges).
- Keeping a persistent CDP session that opens 10+ URLs per minute on a non-interactive cadence.

**Correct patterns (used by the current pipeline):**
- Phase A (notification discovery): Claude navigates the real linkedin-agent MCP browser (headed Chrome, real fingerprint) to `/notifications/`, extracts data from the rendered DOM via a single `browser_run_code` evaluate. No per-permalink visits.
- Phase B (reply posting): `linkedin_api.py` OAuth endpoint first, linkedin-agent MCP browser fallback.
- Audit: Claude-driven with a small batch (up to 15 posts/run), spaced out, via linkedin-agent MCP only.
- Session check: passive. Navigate, take a snapshot, if it is a login or checkpoint page then STOP and print `SESSION_INVALID`. Never programmatically re-login.

**Files that remain authorized:**
- `scripts/linkedin_api.py` (OAuth, documented API). Keep.
- `scripts/linkedin_cooldown.py` (local DB cooldown tracker, no network). Keep.
- All `skill/engage-linkedin.sh` / `skill/engage-dm-replies.sh` / `skill/audit.sh` flows that route through `mcp__linkedin-agent__*`. Keep.

**Files that were removed on 2026-04-17 because they encoded flagged patterns:**
- `scripts/linkedin_comment_fetch.py`
- `scripts/scan_linkedin_notifications.py` and `scripts/scan_linkedin_notifications.js`
- `scripts/backfill_linkedin_content.py`
- `scripts/linkedin_auth_check.py` (programmatic login/session probe)
- bulk Voyager/CDP helpers in `scripts/linkedin_browser.py` (`discover_notifications`, `audit_post`, `audit_batch`, `unread_dms`, `read_conversation`, `send_dm`)

Do not reintroduce any of the above. If you need a new LinkedIn capability, extend `linkedin_api.py` (if the OAuth API supports it) or add a Claude-driven `mcp__linkedin-agent__` step to the existing shell pipelines. Do not write a new Python CDP helper for LinkedIn.

## Engagement Styles System (DO NOT REMOVE)

All posting and engagement scripts use `scripts/engagement_styles.py` to generate a `STYLES_BLOCK` variable injected into prompts. This is an A/B testing system that tracks which comment style gets the best engagement.

- **NEVER remove `STYLES_BLOCK`** from any `skill/run-*.sh` or `skill/engage*.sh` script
- **NEVER remove `engagement_style`** from DB logging (reply_db.py calls, INSERT statements)
- **NEVER remove or simplify style definitions** in `scripts/engagement_styles.py`
- **NEVER inline style definitions** back into individual scripts; the shared module is the single source of truth
