# ai-powered agentic test execution with tool

- product: Assrt
- slug: ai-powered-agentic-test-execution-with-tool
- generated: 20260412-094634Z

## Concept
- **angle**: Agentic test execution is only as good as the tools the agent can call. Assrt gives its inner test agent exactly 18 browser tools (defined in agent.ts lines 16 to 196) organized into five categories, and the design of that tool vocabulary determines what the agent can and cannot test.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://assrt.ai/t/ai-powered-agentic-test-execution-with-tool",
  "slug": "ai-powered-agentic-test-execution-with-tool",
  "commit_sha": "e6ff981",
  "concept_angle": "The 18 browser tools in agent.ts that define the complete vocabulary of what an agentic test agent can perceive, interact with, and assert against a live browser"
}
```

## Tool summary
```json
{
  "total": 81,
  "by_name": {
    "Agent": 3,
    "Bash": 27,
    "ToolSearch": 3,
    "Read": 34,
    "Grep": 2,
    "WebSearch": 7,
    "Glob": 2,
    "Write": 1,
    "mcp__assrt__assrt_test": 2
  },
  "source_touches": {
    "/Users/matthewdi/assrt": {
      "reads": 33,
      "bash": 20
    },
    "/Users/matthewdi/assrt-mcp": {
      "reads": 11,
      "bash": 3
    }
  }
}
```