"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

const items = [
  {
    q: "Does it actually sound like me?",
    a: "Claude drafts in your voice using past posts as grounding. You approve everything before it ships.",
  },
  {
    q: "Will my account get flagged?",
    a: "We operate through your logged-in browser and throttle volume. One platform ban in 18 months of internal use, root-caused to a scraping pattern we've since removed.",
  },
  {
    q: "Which platforms are supported?",
    a: "LinkedIn, Reddit, X, and Hacker News today. Farcaster and Bluesky are on the roadmap.",
  },
  {
    q: "Can I bring my own model?",
    a: "Yes. Swap to any Anthropic model or route through OpenRouter. The prompt scaffolding is model-agnostic.",
  },
  {
    q: "How do I cancel?",
    a: "One click. We keep your drafts for 30 days in case you change your mind.",
  },
];

export function Faq() {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-3xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            FAQ
          </p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
            The obvious questions
          </h2>
        </div>
        <div className="space-y-3">
          {items.map((item, i) => {
            const isOpen = open === i;
            return (
              <div
                key={item.q}
                className="overflow-hidden rounded-xl border border-border bg-card"
              >
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : i)}
                  className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left"
                >
                  <span className="font-medium text-foreground">{item.q}</span>
                  <ChevronDown
                    className={cn(
                      "h-5 w-5 flex-shrink-0 text-muted-foreground transition-transform",
                      isOpen && "rotate-180",
                    )}
                  />
                </button>
                <div
                  className={cn(
                    "grid transition-all duration-200 ease-out",
                    isOpen
                      ? "grid-rows-[1fr] opacity-100"
                      : "grid-rows-[0fr] opacity-0",
                  )}
                >
                  <div className="overflow-hidden">
                    <p className="px-6 pb-6 text-sm text-muted-foreground">
                      {item.a}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
