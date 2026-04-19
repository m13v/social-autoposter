"use client";

import { Mail } from "lucide-react";
import { useState } from "react";

export function Newsletter() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  return (
    <section className="px-6 py-24">
      <div className="mx-auto flex max-w-2xl flex-col items-center rounded-3xl border border-border bg-card p-10 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Mail className="h-5 w-5" />
        </div>
        <h2 className="mt-4 text-3xl font-semibold">Monthly shipping log</h2>
        <p className="mt-2 max-w-md text-muted-foreground">One email a month. What shipped, what broke, what's next. No fluff.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (email) setSent(true);
          }}
          className="mt-6 flex w-full max-w-md flex-col gap-2 sm:flex-row"
        >
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            className="flex-1 rounded-full border border-border bg-background px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
          <button type="submit" className="rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground">
            {sent ? "Subscribed!" : "Subscribe"}
          </button>
        </form>
      </div>
    </section>
  );
}
