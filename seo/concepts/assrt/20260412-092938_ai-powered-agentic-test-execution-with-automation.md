# ai-powered agentic test execution with automation

- product: Assrt
- slug: ai-powered-agentic-test-execution-with-automation
- generated: 20260412-092938Z

## Concept
- **angle**: Assrt's MCP server turns test execution into a composable tool call, enabling AI coding agents to run a closed code-test-diagnose-fix automation loop with zero human handoff
- **source**: /Users/matthewdi/assrt-mcp/src/mcp/server.ts (lines 306-452, tool definitions)
- **anchor_fact**: The MCP server registers exactly 4 tools (assrt_test, assrt_plan, assrt_diagnose, assrt_analyze_video) on a McpServer instance from @modelcontextprotocol/sdk with stdio transport, invoked via `npx assrt-mcp`, making any MCP client able to embed QA as a tool call
- **serp_gap**: Every top SERP result describes agentic testing as a standalone platform with proprietary UI. None describe it as a composable tool that plugs into existing AI coding workflows via MCP, where the coding agent (not a separate testing platform) orchestrates the test-fix loop.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://assrt.ai/t/ai-powered-agentic-test-execution-with-automation",
  "slug": "ai-powered-agentic-test-execution-with-automation",
  "commit_sha": "cd5590e",
  "concept_angle": "Assrt's MCP server turns test execution into a composable tool call, enabling AI coding agents to run a closed code-test-diagnose-fix automation loop with zero human handoff"
}
```

## Tool summary
```json
{
  "total": 34,
  "by_name": {
    "Agent": 3,
    "ToolSearch": 3,
    "WebSearch": 1,
    "WebFetch": 5,
    "Read": 7,
    "Glob": 2,
    "Bash": 9,
    "Grep": 2,
    "Write": 1,
    "mcp__assrt__assrt_test": 1
  },
  "source_touches": {
    "/Users/matthewdi/assrt": {
      "reads": 7,
      "bash": 2
    },
    "/Users/matthewdi/assrt-mcp": {
      "reads": 2,
      "bash": 0
    }
  }
}
```