"use client";

import { Check } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

type Plan = {
  name: string;
  monthly: number;
  yearly: number;
  features: string[];
  featured?: boolean;
};

const plans: Plan[] = [
  {
    name: "Hobby",
    monthly: 0,
    yearly: 0,
    features: ["10 drafts per month", "1 platform", "Community support"],
  },
  {
    name: "Pro",
    monthly: 29,
    yearly: 290,
    features: [
      "Unlimited drafts",
      "All platforms",
      "A/B style rotation",
      "Priority support",
    ],
    featured: true,
  },
  {
    name: "Studio",
    monthly: 99,
    yearly: 990,
    features: [
      "Everything in Pro",
      "5 seats",
      "Custom engagement styles",
      "SSO",
    ],
  },
];

export function Pricing() {
  const [yearly, setYearly] = useState(false);
  return (
    <section id="pricing" className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-6xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            Pricing
          </p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
            Simple plans, no seat traps
          </h2>
          <div className="mt-8 inline-flex items-center gap-3 rounded-full border border-border bg-background p-1">
            <button
              type="button"
              onClick={() => setYearly(false)}
              className={cn(
                "rounded-full px-4 py-1.5 text-sm transition-colors",
                !yearly
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground",
              )}
            >
              Monthly
            </button>
            <button
              type="button"
              onClick={() => setYearly(true)}
              className={cn(
                "rounded-full px-4 py-1.5 text-sm transition-colors",
                yearly
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground",
              )}
            >
              Yearly (save 17%)
            </button>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {plans.map((p) => (
            <div
              key={p.name}
              className={cn(
                "flex flex-col rounded-2xl border p-8 transition-transform",
                p.featured
                  ? "border-primary bg-card shadow-xl md:-translate-y-4"
                  : "border-border bg-card",
              )}
            >
              {p.featured && (
                <span className="mb-4 inline-flex w-fit rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                  Most popular
                </span>
              )}
              <h3 className="text-xl font-semibold text-foreground">{p.name}</h3>
              <div className="mt-4 flex items-baseline gap-1">
                <span className="text-5xl font-semibold text-foreground">
                  ${yearly ? p.yearly : p.monthly}
                </span>
                <span className="text-muted-foreground">
                  /{yearly ? "yr" : "mo"}
                </span>
              </div>
              <ul className="mt-8 space-y-3">
                {p.features.map((f) => (
                  <li
                    key={f}
                    className="flex items-start gap-2 text-sm text-foreground"
                  >
                    <Check className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                    {f}
                  </li>
                ))}
              </ul>
              <button
                type="button"
                className={cn(
                  "mt-8 rounded-full px-4 py-2.5 text-sm font-medium transition-colors",
                  p.featured
                    ? "bg-primary text-primary-foreground hover:bg-primary/90"
                    : "border border-border bg-background text-foreground hover:bg-muted",
                )}
              >
                Get started
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
