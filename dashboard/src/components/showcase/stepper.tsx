"use client";

import { Check } from "lucide-react";
import { useState } from "react";

const steps = ["Connect account", "Pick voice", "Set schedule", "Review first draft"];

export function Stepper() {
  const [current, setCurrent] = useState(2);
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-3xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Onboarding</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Ninety seconds to first post</h2>
        </div>
        <ol className="flex items-center justify-between">
          {steps.map((s, i) => {
            const done = i < current;
            const active = i === current;
            return (
              <li key={s} className="flex flex-1 items-center last:flex-none">
                <div className="flex flex-col items-center">
                  <button
                    type="button"
                    onClick={() => setCurrent(i)}
                    className={`flex h-10 w-10 items-center justify-center rounded-full border-2 text-sm font-semibold transition-colors ${
                      done ? "border-primary bg-primary text-primary-foreground" :
                      active ? "border-primary bg-background text-primary" :
                      "border-border bg-muted text-muted-foreground"
                    }`}
                  >
                    {done ? <Check className="h-4 w-4" /> : i + 1}
                  </button>
                  <span className={`mt-2 max-w-[7rem] text-center text-xs ${active ? "font-medium" : "text-muted-foreground"}`}>{s}</span>
                </div>
                {i < steps.length - 1 && (
                  <div className={`mb-6 mx-2 h-px flex-1 ${done ? "bg-primary" : "bg-border"}`} />
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
