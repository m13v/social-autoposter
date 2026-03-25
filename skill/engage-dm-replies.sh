#!/usr/bin/env bash
# engage-dm-replies.sh — DM conversation reply loop
# Scans Reddit Chat, LinkedIn Messages, and X/Twitter DMs for new inbound messages,
# then replies to continue the conversation.
# Called by launchd every 4 hours.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
DM_SCRIPT="$REPO_DIR/scripts/dm_conversation.py"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-dm-replies-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== DM Reply Engagement Run: $(date) ==="

# Load config
REDDIT_USERNAME=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(c.get('accounts',{}).get('reddit',{}).get('username',''))" 2>/dev/null || echo "")
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")

# Load projects for context
PROJECTS=$(python3 -c "
import json
c = json.load(open('$REPO_DIR/config.json'))
for p in c.get('projects', []):
    print(f\"- {p['name']}: {p.get('description','')} | website: {p.get('website','')} | github: {p.get('github','')}\")
" 2>/dev/null || echo "")

# ═══════════════════════════════════════════════════════
# Find conversations needing replies across all platforms
# ═══════════════════════════════════════════════════════

# Get conversations where the last message is inbound (they replied, we haven't responded)
PENDING_CONVOS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT d.id as dm_id, d.platform, d.their_author, d.tier,
               d.chat_url, d.their_content as original_comment,
               d.comment_context,
               last_in.content as last_inbound_msg,
               last_in.message_at as inbound_at,
               (SELECT COUNT(*) FROM dm_messages WHERE dm_id = d.id) as total_messages,
               (SELECT json_agg(json_build_object(
                   'direction', m.direction,
                   'content', LEFT(m.content, 300),
                   'author', m.author
               ) ORDER BY m.message_at ASC)
               FROM dm_messages m WHERE m.dm_id = d.id) as conversation_history
        FROM dms d
        JOIN LATERAL (
            SELECT content, message_at FROM dm_messages
            WHERE dm_id = d.id AND direction = 'inbound'
            ORDER BY message_at DESC LIMIT 1
        ) last_in ON true
        LEFT JOIN LATERAL (
            SELECT message_at FROM dm_messages
            WHERE dm_id = d.id AND direction = 'outbound'
            ORDER BY message_at DESC LIMIT 1
        ) last_out ON true
        WHERE d.conversation_status = 'active'
          AND d.status = 'sent'
          AND (last_out.message_at IS NULL OR last_in.message_at > last_out.message_at)
        ORDER BY
            d.tier DESC,
            last_in.message_at ASC
        LIMIT 30
    ) q;" 2>/dev/null || echo "null")

if [ "$PENDING_CONVOS" = "null" ] || [ -z "$PENDING_CONVOS" ]; then
    log "No conversations needing replies. Checking platforms for new inbound messages..."
else
    CONVO_COUNT=$(echo "$PENDING_CONVOS" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    log "Found $CONVO_COUNT conversations needing replies from DB"
fi

# ═══════════════════════════════════════════════════════
# PHASE A: Scan Reddit Chat for new inbound messages
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning Reddit Chat for new inbound messages..."

# Get list of known Reddit DM authors to match against chat rooms
KNOWN_REDDIT_AUTHORS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT string_agg(their_author, ', ')
    FROM dms
    WHERE platform='reddit' AND status='sent' AND conversation_status='active';" 2>/dev/null || echo "")

PHASE_A_PROMPT=$(mktemp)
cat > "$PHASE_A_PROMPT" <<PROMPT_EOF
You are the Social Autoposter DM reply engagement bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Scan for new inbound DM messages and reply to continue conversations

CRITICAL - Browser agent rules:
- Reddit Chat: use mcp__reddit-agent__* tools ONLY
- LinkedIn Messages: use mcp__linkedin-agent__* tools ONLY
- X/Twitter DMs: use mcp__twitter-agent__* tools ONLY
NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
If a tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to other tools.

Our projects (for context when conversations touch relevant topics):
$PROJECTS

## PHASE A: Scan Reddit Chat for new messages

1. Navigate to https://www.reddit.com/chat using the reddit-agent browser
2. Wait for chat sidebar to load (3 seconds)
3. Look for chat rooms with unread indicators (bold text, notification badges)
4. For each chat room with unread messages:
   a. Click into the chat room
   b. Read the latest messages
   c. Identify the other person's username
   d. Log any new inbound messages to the database:
      \`\`\`bash
      cd ~/social-autoposter && python3 scripts/dm_conversation.py log-inbound --author "USERNAME" --content "MESSAGE_TEXT"
      \`\`\`
   e. If no existing DM record exists for this user, the script will tell you. Create one:
      \`\`\`bash
      source ~/social-autoposter/.env
      psql "\$DATABASE_URL" -c "INSERT INTO dms (platform, their_author, status, conversation_status, tier) VALUES ('reddit', 'USERNAME', 'sent', 'active', 1) RETURNING id;"
      \`\`\`
      Then set the chat URL:
      \`\`\`bash
      python3 scripts/dm_conversation.py set-url --author "USERNAME" --url "CHAT_URL"
      \`\`\`

## PHASE B: Scan LinkedIn Messages for new messages

1. Navigate to https://www.linkedin.com/messaging/ using the linkedin-agent browser
2. Look for conversations with unread indicators
3. For each unread conversation:
   a. Click into it, read the latest messages
   b. Identify the sender
   c. Check if this person is in our DM database:
      \`\`\`bash
      cd ~/social-autoposter && python3 scripts/dm_conversation.py find --author "PERSON_NAME"
      \`\`\`
   d. Log inbound messages the same way as Reddit

## PHASE C: Scan X/Twitter DMs for new messages

1. Navigate to https://x.com/messages using the twitter-agent browser
2. Look for conversations with unread indicators
3. Same process as above - log inbound messages

## PHASE D: Reply to all conversations with pending inbound messages

After scanning, query for all conversations needing replies:
\`\`\`bash
cd ~/social-autoposter && python3 scripts/dm_conversation.py pending
\`\`\`

Known conversations from the database that already need replies:
$PENDING_CONVOS

For EACH conversation needing a reply:

### Decide the reply strategy based on tier and context:

**Tier 1 (rapport building):** No links. Ask questions, share experiences, be genuinely curious about their work. Keep it casual and short (1-3 sentences). The goal is to build rapport and find a natural opening.

**Tier 2 (natural mention):** The conversation has touched on something related to our projects. Mention it casually if relevant, like "yeah we've been working on something similar" or "that's actually what [project] does". Don't force it.

**Tier 3 (direct share):** They've asked about our work or a tool. Share the link directly.

### Tier escalation rules:
- If they ask "what are you building?" or "what do you work on?" or express interest -> escalate to T2 or T3
- If they mention a problem one of our projects solves -> escalate to T2
- If they explicitly ask for a link -> escalate to T3
- Update tier: \`python3 scripts/dm_conversation.py set-tier --author "USERNAME" --tier N\`

### Reply guidelines:
- Write like you're texting a coworker. Short. Casual. No em dashes.
- Reference specifics from their message, don't be generic
- Ask follow-up questions to keep the conversation going
- If the conversation is going stale or they sent a one-word reply, it's okay to let it rest
- Never send more than 2-3 sentences per reply
- If they shared something cool, acknowledge it genuinely

### Send the reply:

**Reddit Chat** (mcp__reddit-agent__* tools):
1. Navigate to the chat room (use chat_url if available, or find via sidebar)
2. Type the reply in the message input
3. Press Enter to send

**LinkedIn Messages** (mcp__linkedin-agent__* tools):
1. Navigate to the conversation
2. Type and send

**X/Twitter DMs** (mcp__twitter-agent__* tools):
1. Navigate to the conversation
2. Type and send

### After each reply, log it:
\`\`\`bash
cd ~/social-autoposter && python3 scripts/dm_conversation.py log-outbound --author "USERNAME" --content "YOUR_REPLY_TEXT"
\`\`\`

### Skip conditions (mark conversation as stale):
- They haven't replied in 7+ days and the conversation was surface-level
- They sent a clear ending ("thanks", "bye", "good luck")
- The conversation reached a natural conclusion
\`\`\`bash
python3 scripts/dm_conversation.py set-status --author "USERNAME" --status stale
\`\`\`

After processing all conversations, print a summary:
- How many new inbound messages found per platform
- How many replies sent
- How many conversations escalated (tier changes)
- How many marked stale
PROMPT_EOF

gtimeout 5400 claude -p "$(cat "$PHASE_A_PROMPT")" --max-turns 500 2>&1 | tee -a "$LOG_FILE" || log "WARNING: DM reply claude exited with code $?"
rm -f "$PHASE_A_PROMPT"

# ═══════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════
DM_SUMMARY=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_build_object(
        'total_convos', (SELECT COUNT(*) FROM dms WHERE conversation_status='active'),
        'total_messages', (SELECT COUNT(*) FROM dm_messages),
        'inbound', (SELECT COUNT(*) FROM dm_messages WHERE direction='inbound'),
        'outbound', (SELECT COUNT(*) FROM dm_messages WHERE direction='outbound'),
        'tier1', (SELECT COUNT(*) FROM dms WHERE tier=1 AND conversation_status='active'),
        'tier2', (SELECT COUNT(*) FROM dms WHERE tier=2 AND conversation_status='active'),
        'tier3', (SELECT COUNT(*) FROM dms WHERE tier=3 AND conversation_status='active'),
        'stale', (SELECT COUNT(*) FROM dms WHERE conversation_status='stale')
    );" 2>/dev/null || echo "{}")

log "DM pipeline summary: $DM_SUMMARY"

# Delete old logs
find "$LOG_DIR" -name "engage-dm-replies-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== DM reply engagement complete: $(date) ==="
