"use client";

import { Check, Circle, Clock } from "lucide-react";

const events = [
  { date: "2025-Q1", title: "Private alpha", body: "Five teams, LinkedIn only.", state: "done" },
  { date: "2025-Q2", title: "Reddit + X", body: "Engagement rotation ships.", state: "done" },
  { date: "2025-Q3", title: "Approval dashboard", body: "Next.js-based review queue.", state: "done" },
  { date: "2026-Q1", title: "Scheduler API", body: "Control plane for launchd / systemd.", state: "current" },
  { date: "2026-Q2", title: "Farcaster + Bluesky", body: "Two more platforms, same pipeline.", state: "next" },
];

const iconFor = (s: string) => (s === "done" ? Check : s === "current" ? Clock : Circle);
const colorFor = (s: string) =>
  s === "done" ? "bg-primary text-primary-foreground" : s === "current" ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground";

export function Timeline() {
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-3xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Roadmap</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">What we're building</h2>
        </div>
        <ol className="relative space-y-8 border-l border-border pl-8">
          {events.map((e) => {
            const Icon = iconFor(e.state);
            return (
              <li key={e.title} className="relative">
                <span className={`absolute -left-[42px] flex h-8 w-8 items-center justify-center rounded-full ring-4 ring-background ${colorFor(e.state)}`}>
                  <Icon className="h-4 w-4" />
                </span>
                <time className="font-mono text-xs uppercase tracking-wider text-muted-foreground">{e.date}</time>
                <h3 className="mt-1 font-semibold">{e.title}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{e.body}</p>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
