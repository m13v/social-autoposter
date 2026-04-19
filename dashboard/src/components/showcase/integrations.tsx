const integrations = [
  { name: "LinkedIn", color: "bg-blue-500" },
  { name: "Reddit", color: "bg-orange-500" },
  { name: "X / Twitter", color: "bg-zinc-900" },
  { name: "Hacker News", color: "bg-orange-600" },
  { name: "Farcaster", color: "bg-purple-500" },
  { name: "Bluesky", color: "bg-sky-500" },
  { name: "Slack", color: "bg-fuchsia-500" },
  { name: "Discord", color: "bg-indigo-500" },
  { name: "Webhook", color: "bg-emerald-500" },
  { name: "PostHog", color: "bg-amber-500" },
  { name: "Claude", color: "bg-orange-400" },
  { name: "OpenRouter", color: "bg-rose-500" },
];

export function Integrations() {
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-6xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Integrations</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Works with what you already use</h2>
        </div>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
          {integrations.map((i) => (
            <div key={i.name} className="group flex flex-col items-center gap-3 rounded-xl border border-border bg-card p-6 transition-colors hover:bg-accent/40">
              <div className={`h-10 w-10 rounded-lg ${i.color} transition-transform group-hover:scale-110`} />
              <span className="text-sm font-medium">{i.name}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
