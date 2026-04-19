const messages = [
  { who: "bot", name: "Autoposter", text: "Found 3 trending threads matching 'AI observability' in r/MachineLearning. Draft comments?" },
  { who: "user", name: "You", text: "Yes, three variants. Tone: helpful, not salesy." },
  { who: "bot", name: "Autoposter", text: "Drafted. Style rotation: Technical-Detail -> Shared-Experience -> Concise-Take. Queued for your review." },
];

export function Chat() {
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-3xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Copilot</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Talk to the pipeline</h2>
        </div>
        <div className="space-y-4 rounded-2xl border border-border bg-card p-6">
          {messages.map((m, i) => (
            <div key={i} className={`flex gap-3 ${m.who === "user" ? "justify-end" : ""}`}>
              {m.who === "bot" && <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">AI</div>}
              <div className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm ${m.who === "user" ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
                <p className="mb-1 text-xs opacity-70">{m.name}</p>
                {m.text}
              </div>
              {m.who === "user" && <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">U</div>}
            </div>
          ))}
          <div className="mt-4 flex items-center gap-2 rounded-full border border-border bg-background p-1 pl-4">
            <input placeholder="Message the pipeline..." className="flex-1 bg-transparent text-sm outline-none" />
            <button className="rounded-full bg-primary px-4 py-1.5 text-xs text-primary-foreground">Send</button>
          </div>
        </div>
      </div>
    </section>
  );
}
