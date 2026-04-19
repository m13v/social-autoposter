const entries = [
  {
    date: "2026-04-17",
    version: "v1.8.0",
    tag: "feature",
    title: "Scheduler control plane",
    body: "Unified launchd + systemd drivers behind one HTTP API.",
  },
  {
    date: "2026-04-12",
    version: "v1.7.3",
    tag: "fix",
    title: "Reddit FIFO deadlock",
    body: "BSD grep on stale /tmp FIFOs no longer hangs runs.",
  },
  {
    date: "2026-04-09",
    version: "v1.7.0",
    tag: "feature",
    title: "Engagement style A/B",
    body: "Seven styles rotate automatically, tracked per reply.",
  },
  {
    date: "2026-04-02",
    version: "v1.6.1",
    tag: "security",
    title: "Session drift detection",
    body: "Auto-pause when platform session expires mid-run.",
  },
];

const tagColor = (t: string) =>
  t === "feature" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" :
  t === "fix" ? "bg-amber-500/15 text-amber-700 dark:text-amber-400" :
  "bg-blue-500/15 text-blue-700 dark:text-blue-400";

export function Changelog() {
  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-3xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Changelog</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">We ship in public</h2>
        </div>
        <div className="space-y-8">
          {entries.map((e) => (
            <article key={e.version} className="rounded-2xl border border-border bg-card p-6">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <time className="font-mono text-muted-foreground">{e.date}</time>
                <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-xs">{e.version}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${tagColor(e.tag)}`}>{e.tag}</span>
              </div>
              <h3 className="mt-3 text-lg font-semibold">{e.title}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{e.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
