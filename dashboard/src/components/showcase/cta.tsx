"use client";

import { ArrowRight } from "lucide-react";

export function Cta() {
  return (
    <section className="px-6 py-24">
      <div className="relative mx-auto max-w-4xl overflow-hidden rounded-3xl border border-border bg-gradient-to-br from-primary/10 via-background to-background p-12 text-center sm:p-16">
        <div
          aria-hidden
          className="absolute -left-32 -top-32 h-64 w-64 rounded-full bg-primary/20 blur-3xl"
        />
        <div
          aria-hidden
          className="absolute -bottom-32 -right-32 h-64 w-64 rounded-full bg-primary/20 blur-3xl"
        />
        <h2 className="text-balance text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
          Ready to stop posting by hand?
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-balance text-muted-foreground">
          Install the skill, connect one account, and watch the first draft land in under 90 seconds.
        </p>
        <div className="mt-10 flex justify-center">
          <button
            type="button"
            className="group relative inline-flex items-center gap-2 overflow-hidden rounded-full px-8 py-4 text-sm font-medium text-primary-foreground [background:conic-gradient(from_var(--shiny-angle),hsl(var(--primary))_0%,hsl(var(--primary))_40%,hsl(var(--accent-foreground))_50%,hsl(var(--primary))_60%,hsl(var(--primary))_100%)] [animation:shiny-spin_4s_linear_infinite]"
          >
            <span className="relative z-10 flex items-center gap-2">
              Start free
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </span>
          </button>
        </div>
      </div>
    </section>
  );
}
