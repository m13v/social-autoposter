# manual playwright alternative open source

- product: Assrt
- slug: manual-playwright-alternative-open-source
- generated: 20260412-152253Z

## Concept
- **angle**: Assrt keeps Playwright as the execution engine but replaces manual test authoring with plain-text `#Case N:` scenarios that an AI agent drives through 18 Playwright MCP tools at runtime. You never write a selector or an assertion function.
- **source**: /Users/matthewdi/assrt-mcp/src/core/agent.ts (parseScenarios function, TOOLS array, agent execution loop)
- **anchor_fact**: The scenario parser in agent.ts splits test plans using the regex `/(?:#?\s*(?:Scenario|Test|Case))\s*\d*[:.]\s*/gi`, feeding each case to an agent with 18 tools and a 60-step execution limit. The entire MCP package is MIT licensed at v0.4.1-beta.5 on npm as @assrt-ai/assrt.
- **serp_gap**: Every top-ranking "Playwright alternative" article treats Playwright itself as the problem and recommends switching to Cypress, Selenium, or a proprietary AI SaaS. None frame the real problem as manual test authoring and maintenance. None cover open-source AI agents that drive Playwright from natural language at runtime.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://assrt.ai/alternative/manual-playwright-alternative-open-source",
  "slug": "manual-playwright-alternative-open-source",
  "commit_sha": "fdbf335",
  "concept_angle": "Assrt keeps Playwright as the engine but replaces manual test authoring with plain-text scenarios driven by an open-source AI agent with 18 Playwright MCP tools"
}
```

## Tool summary
```json
{
  "total": 91,
  "by_name": {
    "Agent": 3,
    "Bash": 40,
    "Read": 35,
    "ToolSearch": 1,
    "WebSearch": 3,
    "WebFetch": 8,
    "Write": 1
  },
  "source_touches": {
    "/Users/matthewdi/assrt": {
      "reads": 35,
      "bash": 36
    },
    "/Users/matthewdi/assrt-mcp": {
      "reads": 16,
      "bash": 12
    }
  }
}
```