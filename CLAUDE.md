# Social Autoposter

## SKILL.md Architecture - Single Source of Truth

There is ONE `SKILL.md` at the repo root. `skill/SKILL.md` is a **symlink** to `../SKILL.md`.

**When editing the skill instructions, ONLY edit the root `SKILL.md`.** Never create or overwrite `skill/SKILL.md` as a regular file.

### Why this matters

Claude Code loads `skill/SKILL.md` (via `~/.claude/skills/social-autoposter -> ~/social-autoposter/skill/`). Previously, `skill/SKILL.md` was an independent copy that drifted from the root. Edits to the root (e.g. removing rate limits) never reached the loaded skill, causing the same bugs to reappear across conversations.

### How it works

- `skill/SKILL.md` -> `../SKILL.md` (symlink, checked into git)
- `bin/cli.js` re-creates this symlink during both `init` and `update`, so npm users also get the symlink after the copy step
- `setup/SKILL.md` is a separate file (setup wizard instructions, not the main skill)

### No daily rate limit

There is no daily post rate limit enforced by the skill. Platform-specific API limits apply naturally. Do NOT re-add a "Max N posts per 24 hours" line to SKILL.md.
