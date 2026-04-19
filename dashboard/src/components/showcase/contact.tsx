"use client";

import { useState } from "react";

export function Contact() {
  const [sent, setSent] = useState(false);
  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-5xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Contact</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Say hi</h2>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setSent(true);
          }}
          className="mx-auto grid max-w-2xl grid-cols-1 gap-4 md:grid-cols-2"
        >
          <div className="md:col-span-1">
            <label className="mb-1.5 block text-sm font-medium">First name</label>
            <input className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring" />
          </div>
          <div className="md:col-span-1">
            <label className="mb-1.5 block text-sm font-medium">Last name</label>
            <input className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring" />
          </div>
          <div className="md:col-span-2">
            <label className="mb-1.5 block text-sm font-medium">Email</label>
            <input type="email" required className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring" />
          </div>
          <div className="md:col-span-2">
            <label className="mb-1.5 block text-sm font-medium">Message</label>
            <textarea rows={5} className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring" />
          </div>
          <div className="md:col-span-2 flex items-center justify-between">
            <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
              <input type="checkbox" className="h-4 w-4 rounded border-border" />
              Subscribe to updates
            </label>
            <button type="submit" className="rounded-full bg-primary px-6 py-2 text-sm font-medium text-primary-foreground">
              {sent ? "Sent!" : "Send message"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
