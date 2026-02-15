#!/bin/bash
# setup.sh — Set up social-autoposter symlinks and launchd agents
set -euo pipefail

REPO_DIR="$HOME/social-autoposter"

echo "Setting up social-autoposter..."

# Create logs directory
mkdir -p "$REPO_DIR/skill/logs"

# Initialize DB from schema if it doesn't exist
if [ ! -f "$REPO_DIR/social_posts.db" ]; then
    echo "Creating database from schema.sql..."
    sqlite3 "$REPO_DIR/social_posts.db" < "$REPO_DIR/schema.sql"
fi

# Create symlinks
echo "Creating symlinks..."

# Skill directory
rm -rf "$HOME/.claude/skills/social-autoposter"
ln -s "$REPO_DIR/skill" "$HOME/.claude/skills/social-autoposter"
echo "  ~/.claude/skills/social-autoposter -> $REPO_DIR/skill"

# Database
rm -f "$HOME/.claude/social_posts.db"
ln -s "$REPO_DIR/social_posts.db" "$HOME/.claude/social_posts.db"
echo "  ~/.claude/social_posts.db -> $REPO_DIR/social_posts.db"

# LaunchAgent plists
rm -f "$HOME/Library/LaunchAgents/com.m13v.social-autoposter.plist"
rm -f "$HOME/Library/LaunchAgents/com.m13v.social-stats.plist"
ln -s "$REPO_DIR/launchd/com.m13v.social-autoposter.plist" "$HOME/Library/LaunchAgents/com.m13v.social-autoposter.plist"
ln -s "$REPO_DIR/launchd/com.m13v.social-stats.plist" "$HOME/Library/LaunchAgents/com.m13v.social-stats.plist"
echo "  ~/Library/LaunchAgents/com.m13v.social-autoposter.plist -> $REPO_DIR/launchd/..."
echo "  ~/Library/LaunchAgents/com.m13v.social-stats.plist -> $REPO_DIR/launchd/..."

# Reload launchd agents
echo "Loading launchd agents..."
launchctl unload "$HOME/Library/LaunchAgents/com.m13v.social-autoposter.plist" 2>/dev/null || true
launchctl unload "$HOME/Library/LaunchAgents/com.m13v.social-stats.plist" 2>/dev/null || true
launchctl load "$HOME/Library/LaunchAgents/com.m13v.social-autoposter.plist"
launchctl load "$HOME/Library/LaunchAgents/com.m13v.social-stats.plist"

echo "Done. Verify with: launchctl list | grep social"
