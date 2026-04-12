# auto-discovers test scenarios by crawling automation

- product: Assrt
- slug: auto-discovers-test-scenarios-by-crawling-automation
- generated: 20260412-061416Z

## Concept
- **angle**: Assrt uses a two-tier AI prompt architecture (PLAN_SYSTEM_PROMPT for initial URLs, DISCOVERY_SYSTEM_PROMPT for crawled pages) that generates deliberately smaller test cases for discovered pages, making crawl-based discovery fast enough to automate on every commit.
- **source**: /Users/matthewdi/assrt-mcp/src/core/agent.ts lines 216-267 (the two system prompts) and lines 515-548 (generateDiscoveryCases)
- **anchor_fact**: The DISCOVERY_SYSTEM_PROMPT constrains output to 1-2 cases of 3-4 actions and explicitly bans login/signup/CSS/performance tests, while PLAN_SYSTEM_PROMPT allows 5-8 cases of 3-5 actions. This asymmetry, combined with the browserBusy scheduling flag and three concurrent discovery slots, is what makes per-commit automation viable.
- **serp_gap**: Top results explain what crawl-based test discovery is but none address the engineering that makes it automatable: prompt budgeting between initial vs discovered pages, event-driven architecture (page_discovered, discovered_cases_complete events), or the between-scenario scheduling model that prevents discovery from blocking test execution.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://assrt.ai/t/auto-discovers-test-scenarios-by-crawling-automation",
  "slug": "auto-discovers-test-scenarios-by-crawling-automation",
  "commit_sha": "2c04e3f",
  "concept_angle": "Assrt uses a two-tier AI prompt system (thorough for initial URLs, minimal for crawled pages) that makes crawl-based test discovery fast enough to automate on every commit"
}
```

## Tool summary
```json
{
  "total": 88,
  "by_name": {
    "Agent": 3,
    "Bash": 37,
    "ToolSearch": 2,
    "Read": 31,
    "WebSearch": 3,
    "Grep": 2,
    "WebFetch": 7,
    "Glob": 1,
    "Write": 1,
    "mcp__assrt__assrt_test": 1
  },
  "source_touches": {
    "/Users/matthewdi/assrt": {
      "reads": 31,
      "bash": 26
    },
    "/Users/matthewdi/assrt-mcp": {
      "reads": 11,
      "bash": 10
    }
  }
}
```