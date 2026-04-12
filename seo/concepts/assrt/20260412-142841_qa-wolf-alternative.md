# qa wolf alternative

- product: Assrt
- slug: qa-wolf-alternative
- generated: 20260412-142841Z

## Concept
- **angle**: Assrt is an MCP server that turns your AI coding agent into your QA engineer. The same agent that writes your code tests it in a real browser, with no dashboard, no human QA team, and no context switch.
- **source**: /Users/matthewdi/assrt-mcp/src/mcp/server.ts lines 306-323 (3 MCP tool definitions: assrt_test, assrt_plan, assrt_diagnose registered on an McpServer instance)
- **anchor_fact**: server.ts registers 3 MCP tools on a standard McpServer. When assrt_test runs, it writes the test plan to /tmp/assrt/scenario.md as plain markdown. A file watcher in scenario-files.ts (line 97) auto-syncs edits back to Firestore with a 1-second debounce. Your coding agent can read, edit, and re-run the scenario using its normal file tools.
- **serp_gap**: Every top "QA Wolf alternative" page lists other managed services (Bug0, Testlio) or DIY tools (Mabl, Testsigma). None mention MCP, AI coding agent integration, or the concept that the agent writing your code can also test it in a real browser without switching to a dashboard.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://assrt.ai/alternative/qa-wolf-alternative",
  "slug": "qa-wolf-alternative",
  "commit_sha": "ec5018e",
  "concept_angle": "Assrt is an MCP server that turns your AI coding agent into your QA engineer, replacing QA Wolf's $8K/mo managed service with three tool calls in your existing IDE workflow"
}
```

## Tool summary
```json
{
  "total": 31,
  "by_name": {
    "Agent": 2,
    "ToolSearch": 3,
    "WebSearch": 2,
    "WebFetch": 3,
    "Glob": 3,
    "Read": 6,
    "Bash": 9,
    "Write": 1,
    "mcp__assrt__assrt_test": 2
  },
  "source_touches": {
    "/Users/matthewdi/assrt": {
      "reads": 6,
      "bash": 3
    },
    "/Users/matthewdi/assrt-mcp": {
      "reads": 5,
      "bash": 2
    }
  }
}
```