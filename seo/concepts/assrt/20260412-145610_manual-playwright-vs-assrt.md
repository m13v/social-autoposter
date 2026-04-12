# manual playwright vs assrt

- product: Assrt
- slug: manual-playwright-vs-assrt
- generated: 20260412-145610Z

## Concept
- **angle**: Assrt uses Playwright's browser engine underneath but eliminates the code layer entirely; tests are plain English scenarios interpreted by an AI agent at runtime via tool calls, producing no .spec.ts files, no CSS selectors, and no page object models.
- **source**: /Users/matthewdi/assrt-mcp/src/core/agent.ts (TestAgent class parses `#Case N:` markers via regex around line 550, sends each scenario to Claude Haiku which responds with tool calls like `click`, `type_text`, `snapshot`, `assert` routed through Playwright MCP)
- **anchor_fact**: The TestAgent in agent.ts never invokes a code generation function. It splits scenarios on the regex `/(?:#?\s*(?:Scenario|Test|Case))\s*\d*[:.]\s*/gi`, then the LLM agent targets elements using accessibility tree refs (e.g. `ref: "e5"`) instead of CSS selectors. Zero lines of test code are ever written to disk.
- **serp_gap**: Every top result discusses AI helping you write better Playwright code or generate .spec.ts files faster. None covers the approach of removing the code layer entirely while keeping Playwright's browser engine, which is what Assrt does.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://assrt.ai/alternative/manual-playwright-vs-assrt",
  "slug": "manual-playwright-vs-assrt",
  "commit_sha": "9ea9a83",
  "concept_angle": "Assrt uses Playwright's browser engine but eliminates the code layer entirely; tests are plain English interpreted by an AI agent at runtime, producing zero .spec.ts files."
}
```

## Tool summary
```json
{
  "total": 76,
  "by_name": {
    "Agent": 2,
    "Bash": 33,
    "Read": 35,
    "ToolSearch": 1,
    "Glob": 2,
    "WebSearch": 2,
    "Write": 1
  },
  "source_touches": {
    "/Users/matthewdi/assrt": {
      "reads": 35,
      "bash": 30
    },
    "/Users/matthewdi/assrt-mcp": {
      "reads": 13,
      "bash": 9
    }
  }
}
```