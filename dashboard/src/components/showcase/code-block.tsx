"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

const snippet = `# Install the skill
npm install -g @m13v/social-autoposter

# Connect your LinkedIn
autoposter connect linkedin

# Queue a draft
autoposter draft --platform=linkedin \\
  --topic="AI observability" \\
  --style=shared-experience

# Review at http://localhost:3877`;

export function CodeBlock() {
  const [copied, setCopied] = useState(false);
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-3xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Quickstart</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Four commands to first draft</h2>
        </div>
        <div className="overflow-hidden rounded-xl border border-border bg-zinc-950 shadow-2xl">
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2">
            <div className="flex gap-1.5">
              <div className="h-3 w-3 rounded-full bg-red-500/60" />
              <div className="h-3 w-3 rounded-full bg-amber-500/60" />
              <div className="h-3 w-3 rounded-full bg-emerald-500/60" />
            </div>
            <span className="font-mono text-xs text-zinc-400">bash</span>
            <button
              onClick={() => {
                navigator.clipboard.writeText(snippet);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
              className="inline-flex items-center gap-1.5 rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
            >
              {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <pre className="overflow-x-auto p-6 font-mono text-sm leading-relaxed text-zinc-100">
            <code>{snippet}</code>
          </pre>
        </div>
      </div>
    </section>
  );
}
