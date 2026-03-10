#!/bin/bash
# setup.sh — Set up social-autoposter symlinks and launchd agents
set -euo pipefail

REPO_DIR="$HOME/social-autoposter"

echo "Setting up social-autoposter..."

# Create logs directory
mkdir -p "$REPO_DIR/skill/logs"

# Create symlinks
echo "Creating symlinks..."

# Skill directory
rm -rf "$HOME/.claude/skills/social-autoposter"
ln -s "$REPO_DIR/skill" "$HOME/.claude/skills/social-autoposter"
echo "  ~/.claude/skills/social-autoposter -> $REPO_DIR/skill"

# LaunchAgent plists
rm -f "$HOME/Library/LaunchAgents/com.m13v.social-autoposter.plist"
rm -f "$HOME/Library/LaunchAgents/com.m13v.social-stats.plist"
rm -f "$HOME/Library/LaunchAgents/com.m13v.social-engage.plist"
ln -s "$REPO_DIR/launchd/com.m13v.social-autoposter.plist" "$HOME/Library/LaunchAgents/com.m13v.social-autoposter.plist"
ln -s "$REPO_DIR/launchd/com.m13v.social-stats.plist" "$HOME/Library/LaunchAgents/com.m13v.social-stats.plist"
ln -s "$REPO_DIR/launchd/com.m13v.social-engage.plist" "$HOME/Library/LaunchAgents/com.m13v.social-engage.plist"
echo "  ~/Library/LaunchAgents/com.m13v.social-autoposter.plist -> $REPO_DIR/launchd/..."
echo "  ~/Library/LaunchAgents/com.m13v.social-stats.plist -> $REPO_DIR/launchd/..."
echo "  ~/Library/LaunchAgents/com.m13v.social-engage.plist -> $REPO_DIR/launchd/..."

# Reload launchd agents
echo "Loading launchd agents..."
launchctl unload "$HOME/Library/LaunchAgents/com.m13v.social-autoposter.plist" 2>/dev/null || true
launchctl unload "$HOME/Library/LaunchAgents/com.m13v.social-stats.plist" 2>/dev/null || true
launchctl unload "$HOME/Library/LaunchAgents/com.m13v.social-engage.plist" 2>/dev/null || true
launchctl load "$HOME/Library/LaunchAgents/com.m13v.social-autoposter.plist"
launchctl load "$HOME/Library/LaunchAgents/com.m13v.social-stats.plist"
launchctl load "$HOME/Library/LaunchAgents/com.m13v.social-engage.plist"

echo "Done. Verify with: launchctl list | grep social"
