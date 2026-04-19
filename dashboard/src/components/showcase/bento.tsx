"use client";

import { cn } from "@/lib/utils";
import {
  Bot,
  Calendar,
  CircuitBoard,
  MessageSquare,
  Rocket,
  Sparkles,
} from "lucide-react";
import type { ReactNode } from "react";

type Feature = {
  title: string;
  description: string;
  icon: ReactNode;
  className: string;
};

const features: Feature[] = [
  {
    title: "Scheduled posts",
    description: "Queue content weeks ahead across LinkedIn, Reddit, and X.",
    icon: <Calendar className="h-6 w-6" />,
    className: "md:col-span-2 md:row-span-2",
  },
  {
    title: "Auto-engage",
    description: "Drop in-style replies the moment a target account posts.",
    icon: <MessageSquare className="h-6 w-6" />,
    className: "md:col-span-1",
  },
  {
    title: "Agentic drafting",
    description: "Claude writes copy that matches your voice, not the model's.",
    icon: <Bot className="h-6 w-6" />,
    className: "md:col-span-1",
  },
  {
    title: "Style A/B",
    description: "Seven engagement styles rotate automatically.",
    icon: <Sparkles className="h-6 w-6" />,
    className: "md:col-span-1",
  },
  {
    title: "Dashboard",
    description: "Approve, edit, or bin drafts from one screen.",
    icon: <CircuitBoard className="h-6 w-6" />,
    className: "md:col-span-1",
  },
  {
    title: "Launch ready",
    description: "Plug in once; the pipeline keeps firing.",
    icon: <Rocket className="h-6 w-6" />,
    className: "md:col-span-2",
  },
];

export function Bento() {
  return (
    <section id="bento" className="px-6 py-24">
      <div className="mx-auto max-w-6xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            Features
          </p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
            Everything the pipeline already does
          </h2>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-4 md:auto-rows-[14rem]">
          {features.map((f) => (
            <div
              key={f.title}
              className={cn(
                "group relative flex flex-col justify-between overflow-hidden rounded-2xl border border-border bg-card p-6 transition-colors hover:bg-accent/40",
                f.className,
              )}
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
                {f.icon}
              </div>
              <div>
                <h3 className="text-lg font-semibold text-foreground">
                  {f.title}
                </h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {f.description}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
