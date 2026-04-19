const quotes = [
  { who: "Ava C.", line: "Fired my VA and did not look back." },
  { who: "Marcos V.", line: "Best $29 I spend every month." },
  { who: "Priya S.", line: "Three inbound calls in week one." },
  { who: "Noah P.", line: "Our CTO reposts this thing in our Slack." },
  { who: "Emma W.", line: "Finally a poster that sounds human." },
  { who: "James W.", line: "The approval queue is the killer feature." },
];

export function Marquee() {
  return (
    <section className="overflow-hidden border-y border-border bg-muted/30 py-12">
      <div className="relative">
        <div className="flex animate-[marquee-scroll_40s_linear_infinite] gap-4 hover:[animation-play-state:paused]">
          {[...quotes, ...quotes].map((q, i) => (
            <div key={i} className="flex min-w-[300px] flex-col gap-2 rounded-xl border border-border bg-card p-4">
              <p className="text-sm">&ldquo;{q.line}&rdquo;</p>
              <p className="text-xs text-muted-foreground">{q.who}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
