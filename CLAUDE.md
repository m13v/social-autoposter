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
- **Do NOT add rate limits to SKILL.md** - do not re-add "Max N posts per 24 hours" or any limit language. If SKILL.md does not contain a rate limit step, that is intentional.
- **Do NOT "improve" or "fix" the workflow** - do not renumber steps, do not add safety checks, do not restructure the file.
