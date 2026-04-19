"use client";

import {
  AlertTriangle,
  Check,
  ChevronDown,
  Info,
  Loader2,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

function Subhead({ children }: { children: string }) {
  return <h3 className="mb-4 text-sm font-medium uppercase tracking-wider text-muted-foreground">{children}</h3>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <Subhead>{title}</Subhead>
      <div className="rounded-xl border border-border bg-card p-6">{children}</div>
    </div>
  );
}

export function UiGallery() {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [progress, setProgress] = useState(30);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [toast, setToast] = useState(false);

  useEffect(() => {
    const i = setInterval(() => setProgress((p) => (p >= 100 ? 10 : p + 10)), 700);
    return () => clearInterval(i);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(false), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-6xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">UI primitives</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">The kit we mix and match</h2>
        </div>

        <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
          <Panel title="Buttons">
            <div className="flex flex-wrap gap-3">
              <button className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">Primary</button>
              <button className="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground">Secondary</button>
              <button className="rounded-md border border-border bg-background px-4 py-2 text-sm font-medium">Outline</button>
              <button className="rounded-md px-4 py-2 text-sm font-medium hover:bg-accent">Ghost</button>
              <button className="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground">Destructive</button>
              <button className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground opacity-50" disabled>Disabled</button>
              <button className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading
              </button>
            </div>
          </Panel>

          <Panel title="Badges">
            <div className="flex flex-wrap gap-2">
              <span className="rounded-full bg-primary px-2.5 py-0.5 text-xs font-medium text-primary-foreground">Default</span>
              <span className="rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-secondary-foreground">Secondary</span>
              <span className="rounded-full border border-border px-2.5 py-0.5 text-xs font-medium">Outline</span>
              <span className="rounded-full bg-destructive px-2.5 py-0.5 text-xs font-medium text-destructive-foreground">Destructive</span>
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Live
              </span>
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" /> Beta
              </span>
            </div>
          </Panel>

          <Panel title="Inputs">
            <div className="space-y-3">
              <input placeholder="Text input" className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring" />
              <input type="email" placeholder="email@domain.com" className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm" />
              <select className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm">
                <option>Choose a platform</option>
                <option>LinkedIn</option>
                <option>Reddit</option>
              </select>
              <label className="inline-flex items-center gap-2 text-sm">
                <input type="checkbox" defaultChecked className="h-4 w-4" /> Enable auto-engage
              </label>
              <label className="inline-flex items-center gap-2 text-sm">
                <input type="radio" name="r" defaultChecked /> Aggressive
              </label>
              <label className="inline-flex items-center gap-2 text-sm">
                <input type="radio" name="r" /> Mellow
              </label>
            </div>
          </Panel>

          <Panel title="Alerts">
            <div className="space-y-3">
              <div className="flex items-start gap-3 rounded-md border border-border bg-background p-3 text-sm">
                <Info className="mt-0.5 h-4 w-4 text-primary" />
                <div>
                  <p className="font-medium">Heads up</p>
                  <p className="text-muted-foreground">LinkedIn session expires in 3 days.</p>
                </div>
              </div>
              <div className="flex items-start gap-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-600" />
                <div>
                  <p className="font-medium">Rate limit close</p>
                  <p className="text-muted-foreground">Slowing to stay inside Reddit's per-minute cap.</p>
                </div>
              </div>
              <div className="flex items-start gap-3 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm">
                <Check className="mt-0.5 h-4 w-4 text-emerald-600" />
                <div>
                  <p className="font-medium">Shipped</p>
                  <p className="text-muted-foreground">Draft posted to LinkedIn and 4 subreddits.</p>
                </div>
              </div>
              <div className="flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm">
                <X className="mt-0.5 h-4 w-4 text-destructive" />
                <div>
                  <p className="font-medium">Post rejected</p>
                  <p className="text-muted-foreground">Subreddit moderator removed the comment.</p>
                </div>
              </div>
            </div>
          </Panel>

          <Panel title="Progress">
            <div className="space-y-4">
              <div>
                <div className="mb-1.5 flex items-center justify-between text-xs text-muted-foreground">
                  <span>Daily quota</span><span>{progress}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-muted">
                  <div className="h-full bg-primary transition-[width] duration-500" style={{ width: `${progress}%` }} />
                </div>
              </div>
              <div>
                <div className="mb-1.5 flex items-center justify-between text-xs text-muted-foreground">
                  <span>Storage</span><span>68%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-muted">
                  <div className="h-full w-[68%] bg-gradient-to-r from-emerald-500 to-primary" />
                </div>
              </div>
              <div className="flex items-center gap-3 text-sm">
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                <span className="text-muted-foreground">Syncing drafts...</span>
              </div>
            </div>
          </Panel>

          <Panel title="Dropdown">
            <div className="relative inline-block">
              <button
                onClick={() => setDropdownOpen((v) => !v)}
                className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium"
              >
                Options <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", dropdownOpen && "rotate-180")} />
              </button>
              {dropdownOpen && (
                <div className="absolute left-0 top-full z-10 mt-2 w-48 rounded-md border border-border bg-card p-1 shadow-lg">
                  {["Edit", "Duplicate", "Archive", "Delete"].map((o) => (
                    <button
                      key={o}
                      onClick={() => setDropdownOpen(false)}
                      className={cn(
                        "flex w-full items-center rounded px-3 py-1.5 text-sm hover:bg-accent",
                        o === "Delete" && "text-destructive",
                      )}
                    >
                      {o}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Panel>

          <Panel title="Avatars">
            <div className="flex items-center gap-6">
              <div className="flex -space-x-2">
                {["AC", "MV", "PS", "NP", "+4"].map((i, idx) => (
                  <div
                    key={i}
                    className={cn(
                      "flex h-10 w-10 items-center justify-center rounded-full ring-2 ring-background text-xs font-semibold",
                      idx === 4 ? "bg-muted text-foreground" : "bg-gradient-to-br from-primary/30 to-primary/60 text-foreground",
                    )}
                  >
                    {i}
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <div className="relative">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/20 text-sm font-semibold">AC</div>
                  <div className="absolute bottom-0 right-0 h-3 w-3 rounded-full bg-emerald-500 ring-2 ring-background" />
                </div>
                <span className="text-sm">Ava, online</span>
              </div>
            </div>
          </Panel>

          <Panel title="Toast + Dialog">
            <div className="flex flex-wrap gap-3">
              <button onClick={() => setToast(true)} className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground">
                Fire toast
              </button>
              <button onClick={() => setDialogOpen(true)} className="rounded-md border border-border bg-background px-4 py-2 text-sm">
                Open dialog
              </button>
            </div>
            {toast && (
              <div className="fixed bottom-6 right-6 z-50 flex items-start gap-3 rounded-lg border border-border bg-card p-4 shadow-xl">
                <Sparkles className="mt-0.5 h-4 w-4 text-primary" />
                <div>
                  <p className="text-sm font-medium">Saved!</p>
                  <p className="text-xs text-muted-foreground">Your draft has been queued.</p>
                </div>
              </div>
            )}
            {dialogOpen && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4" onClick={() => setDialogOpen(false)}>
                <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6" onClick={(e) => e.stopPropagation()}>
                  <h4 className="text-lg font-semibold">Delete this draft?</h4>
                  <p className="mt-2 text-sm text-muted-foreground">This can't be undone. The draft won't be recoverable.</p>
                  <div className="mt-6 flex justify-end gap-2">
                    <button onClick={() => setDialogOpen(false)} className="rounded-md border border-border bg-background px-4 py-2 text-sm">
                      Cancel
                    </button>
                    <button onClick={() => setDialogOpen(false)} className="rounded-md bg-destructive px-4 py-2 text-sm text-destructive-foreground">
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            )}
          </Panel>

          <Panel title="Skeletons">
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 animate-pulse rounded-full bg-muted" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
                  <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
                </div>
              </div>
              <div className="h-24 w-full animate-pulse rounded-lg bg-muted" />
              <div className="flex gap-2">
                <div className="h-8 w-20 animate-pulse rounded bg-muted" />
                <div className="h-8 w-20 animate-pulse rounded bg-muted" />
              </div>
            </div>
          </Panel>

          <Panel title="Tooltip">
            <div className="flex items-center gap-4">
              <div className="group relative">
                <button className="rounded-md border border-border bg-background px-4 py-2 text-sm">Hover me</button>
                <div className="pointer-events-none absolute -top-10 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-foreground px-2 py-1 text-xs text-background opacity-0 transition-opacity group-hover:opacity-100">
                  Appears on hover
                </div>
              </div>
              <div className="group relative">
                <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-border bg-background text-xs">?</span>
                <div className="pointer-events-none absolute -top-10 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-foreground px-2 py-1 text-xs text-background opacity-0 transition-opacity group-hover:opacity-100">
                  Tooltip content
                </div>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </section>
  );
}
