# momentic alternative open source

- product: Assrt
- slug: momentic-alternative-open-source
- generated: 20260412-141850Z

## Concept
- **angle**: Assrt is open source down to the test agent's tool definitions. You can read, modify, and extend every tool in agent.ts. Momentic's test execution is a black box you pay $2,500/mo to access.
- **source**: /Users/matthewdi/assrt-mcp/src/core/agent.ts (lines 16-196 define all 15 tools; lines 198-254 contain the system prompt)
- **anchor_fact**: The file agent.ts exports a TOOLS array of 15 Playwright-based tool definitions (navigate, snapshot, click, type_text, select_option, scroll, press_key, wait, screenshot, evaluate, assert, complete_scenario, suggest_improvement, http_request, wait_for_stable). Any developer can fork the repo, add a 16th tool, and the agent will use it on the next run. The Dockerfile is 48 lines.
- **serp_gap**: Every "momentic alternative" page is a listicle or directory. None explain what "open source" actually means for a test tool: the ability to read, audit, and extend the AI agent's tool vocabulary, system prompt, and execution logic.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://assrt.ai/alternative/momentic-alternative-open-source",
  "slug": "momentic-alternative-open-source",
  "commit_sha": "c2e4fa8",
  "concept_angle": "Assrt is open source down to the test agent's 15 tool definitions in agent.ts, which you can read, modify, and extend unlike Momentic's proprietary black box"
}
```

## Tool summary
```json
{
  "total": 104,
  "by_name": {
    "Agent": 3,
    "Bash": 38,
    "Glob": 8,
    "Read": 38,
    "ToolSearch": 2,
    "WebSearch": 3,
    "Grep": 3,
    "WebFetch": 7,
    "Write": 1,
    "mcp__assrt__assrt_test": 1
  },
  "source_touches": {
    "/Users/matthewdi/assrt": {
      "reads": 38,
      "bash": 32
    },
    "/Users/matthewdi/assrt-mcp": {
      "reads": 14,
      "bash": 8
    }
  }
}
```