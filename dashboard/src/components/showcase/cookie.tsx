"use client";

import { useState } from "react";

export function CookieBanner() {
  const [show, setShow] = useState(true);
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-3xl">
        <div className="mb-8 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Consent</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Cookie banner</h2>
        </div>
        <div className={`mx-auto max-w-xl rounded-xl border border-border bg-card p-5 shadow-xl transition-all ${show ? "opacity-100" : "pointer-events-none translate-y-2 opacity-0"}`}>
          <h3 className="font-semibold">We use cookies</h3>
          <p className="mt-1 text-sm text-muted-foreground">Analytics only. We don't sell your data, we don't track you across sites.</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button onClick={() => setShow(false)} className="rounded-md bg-primary px-4 py-1.5 text-sm text-primary-foreground">Accept all</button>
            <button onClick={() => setShow(false)} className="rounded-md border border-border bg-background px-4 py-1.5 text-sm">Only necessary</button>
            <button onClick={() => setShow(false)} className="rounded-md px-4 py-1.5 text-sm text-muted-foreground hover:bg-accent">Preferences</button>
          </div>
        </div>
        {!show && (
          <button onClick={() => setShow(true)} className="mt-6 block mx-auto text-sm text-primary hover:underline">
            Reset banner
          </button>
        )}
      </div>
    </section>
  );
}
