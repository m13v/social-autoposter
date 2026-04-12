# how to ai-powered agentic test execution with

- product: Assrt
- slug: how-to-ai-powered-agentic-test-execution-with
- generated: 20260412-092102Z

## Concept
- **angle**: Assrt's agentic test execution works by having the AI agent read accessibility tree snapshots (not CSS selectors or screenshots) to perceive page state, target elements by ref IDs, and recover from stale refs via fuzzy text matching, creating a closed perception-action-recovery loop that no SERP result explains.
- **source**: /Users/matthewdi/assrt/src/core/agent.ts (lines 198-254 system prompt, lines 305-1002 agent loop), /Users/matthewdi/assrt/src/core/browser.ts (lines 213-219 ref targeting, lines 293-343 fuzzy recovery)
- **anchor_fact**: The agent's showClickAt() function in browser.ts scores candidate elements using a 3-tier fuzzy match (exact match = score 3, partial = 2, word overlap = proportional) across all interactive elements, enabling self-healing without maintaining a selector database.
- **serp_gap**: Every top result says "AI interacts like a human" without explaining the actual perception mechanism. None describe accessibility tree snapshots, ref-based targeting, or how the agent recovers from broken element references. The technical substrate of agentic test execution is completely absent from the SERP.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://assrt.ai/t/how-to-ai-powered-agentic-test-execution-with",
  "slug": "how-to-ai-powered-agentic-test-execution-with",
  "commit_sha": "c0ba439",
  "concept_angle": "Explains the accessibility-tree perception loop that powers agentic test execution, covering ref-based targeting and fuzzy self-healing, which no competing SERP result covers"
}
```

## Tool summary
```json
{
  "total": 100,
  "by_name": {
    "Agent": 3,
    "Bash": 31,
    "Read": 43,
    "Glob": 3,
    "Grep": 3,
    "ToolSearch": 2,
    "WebSearch": 4,
    "WebFetch": 8,
    "Write": 1,
    "mcp__assrt__assrt_test": 2
  },
  "source_touches": {
    "/Users/matthewdi/assrt": {
      "reads": 43,
      "bash": 23
    },
    "/Users/matthewdi/assrt-mcp": {
      "reads": 4,
      "bash": 5
    }
  }
}
```