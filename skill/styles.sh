#!/bin/bash
# Shared engagement styles helper
# Usage: source styles.sh; STYLES_BLOCK=$(generate_styles_block platform context)
# Requires REPO_DIR to be set before sourcing.

generate_styles_block() {
  local platform="$1"
  local context="${2:-posting}"
  python3 -c "import sys; sys.path.insert(0,'$REPO_DIR/scripts'); from engagement_styles import get_styles_prompt, get_content_rules, get_anti_patterns; print(get_styles_prompt('$platform', context='$context')); print(); print('## Content rules'); print(get_content_rules('$platform')); print(); print(get_anti_patterns())" 2>/dev/null || echo "(style module unavailable)"
}
