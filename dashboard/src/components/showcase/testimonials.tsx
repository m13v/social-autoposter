"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ChevronLeft, ChevronRight, Quote } from "lucide-react";
import { useState } from "react";

const items = [
  {
    quote:
      "I stopped caring what day it is. The queue just goes, and my DMs fill up on their own.",
    name: "Ava Carter",
    role: "Founder, Lumen Studio",
  },
  {
    quote:
      "The engagement-style rotation is the thing. Comments sound like me on a Tuesday, not a bot on launch day.",
    name: "Marcos Velez",
    role: "Creator, ShipFast Weekly",
  },
  {
    quote:
      "Plugged it into our LinkedIn on a Monday. By Friday I had three discovery calls booked from cold comments.",
    name: "Priya Shah",
    role: "GTM Lead, Harbour",
  },
];

export function Testimonials() {
  const [i, setI] = useState(0);
  const current = items[i];
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-3xl text-center">
        <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
          Testimonials
        </p>
        <h2 className="mt-2 text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
          What users actually say
        </h2>
        <div className="relative mt-12 rounded-2xl border border-border bg-card p-10 text-left">
          <Quote className="absolute left-6 top-6 h-8 w-8 text-muted-foreground/30" />
          <AnimatePresence mode="wait">
            <motion.div
              key={current.name}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.35 }}
            >
              <p className="text-balance text-xl leading-relaxed text-foreground">
                {current.quote}
              </p>
              <div className="mt-6">
                <p className="font-medium text-foreground">{current.name}</p>
                <p className="text-sm text-muted-foreground">{current.role}</p>
              </div>
            </motion.div>
          </AnimatePresence>
          <div className="mt-8 flex items-center justify-between">
            <div className="flex gap-2">
              {items.map((_, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setI(idx)}
                  aria-label={`Testimonial ${idx + 1}`}
                  className={`h-1.5 rounded-full transition-all ${
                    idx === i ? "w-8 bg-primary" : "w-4 bg-muted-foreground/30"
                  }`}
                />
              ))}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() =>
                  setI((v) => (v - 1 + items.length) % items.length)
                }
                className="rounded-full border border-border p-2 transition-colors hover:bg-muted"
                aria-label="Previous"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => setI((v) => (v + 1) % items.length)}
                className="rounded-full border border-border p-2 transition-colors hover:bg-muted"
                aria-label="Next"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
