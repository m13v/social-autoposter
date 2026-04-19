import { Command, Hash, Plus, Send, Settings } from "lucide-react";

const groups = [
  { label: "Actions", items: [{ icon: Plus, name: "New draft", keys: ["N"] }, { icon: Send, name: "Post now", keys: ["P"] }] },
  { label: "Navigate", items: [{ icon: Hash, name: "Go to inbox", keys: ["G", "I"] }, { icon: Settings, name: "Open settings", keys: ["G", "S"] }] },
];

export function CommandPalette() {
  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-2xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Command palette</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Do anything with keys</h2>
        </div>
        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <Command className="h-4 w-4 text-muted-foreground" />
            <input placeholder="Search commands..." className="flex-1 bg-transparent text-sm outline-none" autoFocus />
            <kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">ESC</kbd>
          </div>
          <div className="p-2">
            {groups.map((g) => (
              <div key={g.label} className="mb-1">
                <p className="px-2 py-1 text-xs uppercase tracking-wider text-muted-foreground">{g.label}</p>
                {g.items.map((it) => (
                  <button key={it.name} className="flex w-full items-center gap-3 rounded px-2 py-2 text-sm hover:bg-accent">
                    <it.icon className="h-4 w-4 text-muted-foreground" />
                    <span className="flex-1 text-left">{it.name}</span>
                    <div className="flex gap-1">
                      {it.keys.map((k) => (
                        <kbd key={k} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">{k}</kbd>
                      ))}
                    </div>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
