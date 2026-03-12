#!/bin/bash
# Social Autoposter - GitHub Issues posting
# Find relevant open issues across GitHub, post helpful comments, self-reply with specific file links.
# Called by launchd every 4 hours.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/skill/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/github-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== GitHub Issues Run: $(date) ===" | tee "$LOG_FILE"

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Also read $REPO_DIR/config.json for accounts, projects, and search_topics.

Run the **Workflow: GitHub Issues** section. Follow every step:
1. Search for relevant issues using topics from config.json -> accounts.github.search_topics
   Rotate through different search topics each run - don't always search the same keywords.
   Use: gh search issues \"TOPIC\" --limit 10 --state open --sort updated
3. Check dedup: SELECT thread_url FROM posts WHERE platform='github_issues'
4. Pick the best 2-3 issues where our experience genuinely adds value
5. Read each issue fully (body + existing comments)
6. Draft helpful comments (follow Content Rules - NEVER use em dashes)
7. Post via: gh issue comment NUMBER -R OWNER/REPO --body \"...\"
8. Log to database
9. Self-reply with a link to a SPECIFIC FILE in our repos (not just the repo homepage).
   Map expertise to files:
   - macOS accessibility/AX/click/screen control -> mediar-ai/mcp-server-macos-use/Sources/MCPServer/main.swift
   - Desktop automation framework/element interaction -> mediar-ai/terminator/crates/terminator/src/element.rs
   - Desktop automation core/Rust -> mediar-ai/terminator/crates/terminator/src/lib.rs
   - MCP server for desktop -> mediar-ai/terminator/crates/terminator-mcp-agent/src/server.rs
   - Screen capture/ScreenCaptureKit -> m13v/macos-session-replay/Sources/SessionReplay/ScreenCaptureService.swift
   - Video encoding/recording -> m13v/macos-session-replay/Sources/SessionReplay/VideoChunkEncoder.swift
   - Voice/transcription/WhisperKit -> m13v/fazm/Desktop/Sources/TranscriptionService.swift
   - Claude API/LLM provider -> m13v/fazm/Desktop/Sources/Providers/ChatProvider.swift
   - Tool execution/function calling -> m13v/fazm/Desktop/Sources/Providers/ChatToolExecutor.swift
   - Floating UI/overlay -> m13v/fazm/Desktop/Sources/FloatingControlBar/FloatingControlBarView.swift
   - Browser lock/multi-agent Playwright -> m13v/browser-lock/playwright-lock.sh
   - User memory/knowledge extraction -> m13v/user-memories/user_memories/db.py
   - Memory embeddings/semantic search -> m13v/user-memories/user_memories/embeddings.py
   - Browser history ingestion -> m13v/user-memories/user_memories/ingestors/history.py
   - Social posting pipeline -> m13v/social-autoposter/skill/SKILL.md
   - Launchd scheduling -> m13v/social-autoposter/launchd/ (directory)
   - Reply scanning -> m13v/social-autoposter/scripts/scan_replies.py
   - Video editing/ffmpeg -> m13v/video-edit/SKILL.md
   - Video upload to social -> m13v/social-media-video-upload
   - Tmux agent orchestration -> m13v/tmux-background-agents/SKILL.md
   - Skill publishing -> m13v/publish-skill
   - Skill registry -> m13v/skill-registry
10. Log self-reply to database too

Post to 2-3 issues per run. Spread across different repos and topics.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: In self-replies, link to SPECIFIC FILES (blob/main/path/to/file.ext), not just repo homepages." --max-turns 50 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

# Clean up old logs (keep last 7 days)
find "$LOG_DIR" -name "github-*.log" -mtime +7 -delete 2>/dev/null || true
