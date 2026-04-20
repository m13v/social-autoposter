"use client";

import { ChevronLeft, ChevronRight, ChevronRight as Slash } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

export function PaginationDemo() {
  const [page, setPage] = useState(3);
  const total = 12;
  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-4xl">
        <div className="mb-8">
          <nav className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <a href="#" className="hover:text-foreground">Dashboard</a>
            <Slash className="h-3.5 w-3.5" />
            <a href="#" className="hover:text-foreground">Drafts</a>
            <Slash className="h-3.5 w-3.5" />
            <span className="text-foreground">LinkedIn</span>
          </nav>
        </div>
        <div className="rounded-2xl border border-border bg-card p-8 text-center">
          <h3 className="text-xl font-semibold">Draft #{page}</h3>
          <p className="mt-2 text-muted-foreground">Showing page {page} of {total}.</p>
          <div className="mt-8 flex items-center justify-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm disabled:opacity-40"
              disabled={page === 1}
            >
              <ChevronLeft className="h-3.5 w-3.5" /> Prev
            </button>
            {[1, 2, 3, 4, 5].map((n) => (
              <button
                key={n}
                onClick={() => setPage(n)}
                className={cn(
                  "h-8 w-8 rounded-md text-sm",
                  page === n ? "bg-primary text-primary-foreground" : "hover:bg-accent",
                )}
              >
                {n}
              </button>
            ))}
            <span className="px-2 text-muted-foreground">...</span>
            <button onClick={() => setPage(total)} className="h-8 w-8 rounded-md text-sm hover:bg-accent">{total}</button>
            <button
              onClick={() => setPage((p) => Math.min(total, p + 1))}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm disabled:opacity-40"
              disabled={page === total}
            >
              Next <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
