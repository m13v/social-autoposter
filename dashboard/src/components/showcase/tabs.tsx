"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

const tabs = [
  { id: "drafts", label: "Drafts", body: "Review queued drafts before they ship. Approve, edit, or archive." },
  { id: "threads", label: "Threads", body: "Break long posts into threads. Preview exactly how they'll render." },
  { id: "replies", label: "Replies", body: "Auto-engagement tails target accounts and drops in-voice comments." },
  { id: "stats", label: "Stats", body: "What got impressions, what converted, what got flagged." },
];

export function TabsDemo() {
  const [active, setActive] = useState(tabs[0].id);
  const current = tabs.find((t) => t.id === active)!;
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-4xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Workflow</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">One dashboard, four jobs</h2>
        </div>
        <div className="flex gap-1 rounded-full border border-border bg-muted/40 p-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setActive(t.id)}
              className={cn(
                "flex-1 rounded-full px-4 py-2 text-sm font-medium transition-colors",
                active === t.id ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="mt-8 rounded-2xl border border-border bg-card p-8">
          <h3 className="text-lg font-semibold">{current.label}</h3>
          <p className="mt-2 text-muted-foreground">{current.body}</p>
        </div>
      </div>
    </section>
  );
}
