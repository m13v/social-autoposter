# ai app builder free

- product: mk0r
- slug: ai-app-builder-free
- generated: 20260412-204047Z

## Concept
- **angle**: mk0r is the only free AI app builder that spins up a full sandboxed VM with Chromium and Playwright for every app, so the AI agent tests your app in a real browser before showing it to you.
- **source**: src/core/freestyle.ts lines 487-497 (baseImageSetup services: Xvfb, Chromium CDP, Playwright MCP, Vite dev server) and src/app/api/vm/build/route.ts
- **anchor_fact**: Each VM includes five services (Xvfb virtual display, Chromium with Chrome DevTools Protocol on port 9222, Playwright MCP on port 3001, ACP agent bridge on port 3002, and a Vite dev server on port 5173), boots from a persisted Firestore snapshot in 2 to 3 seconds, and the AI agent iterates on bugs by reading real browser output before the user ever sees the result.
- **serp_gap**: Every "free AI app builder" comparison evaluates tools on pricing tiers, feature lists, and deployment options. None mention execution isolation, sandboxed VMs, or AI driven browser testing. The concept of the builder testing its own output in a real browser does not appear anywhere in the top results.

## Final JSON
```json
{
  "success": true,
  "page_url": "https://mk0r.com/t/ai-app-builder-free",
  "slug": "ai-app-builder-free",
  "commit_sha": "2ddc12d",
  "concept_angle": "mk0r is the only free AI app builder that spins up a sandboxed VM with Chromium and Playwright so the AI tests its own output in a real browser before showing it to you"
}
```

## Tool summary
```json
{
  "total": 116,
  "by_name": {
    "Agent": 2,
    "Bash": 53,
    "Read": 34,
    "ToolSearch": 1,
    "WebSearch": 5,
    "WebFetch": 11,
    "Glob": 5,
    "Grep": 3,
    "Edit": 1,
    "Write": 1
  },
  "source_touches": {
    "/Users/matthewdi/appmaker": {
      "reads": 22,
      "bash": 26
    }
  }
}
```