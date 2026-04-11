#!/usr/bin/env bash
# pause.sh — Pause all social-autoposter pipelines
# All launchd jobs keep their schedules; they just exit immediately on each fire.
# Resume with: skill/resume.sh
touch "$HOME/.social-paused"
echo "Paused at $(date)" >> "$HOME/.social-paused"
echo "All pipelines PAUSED. Resume with: ~/social-autoposter/skill/resume.sh"
