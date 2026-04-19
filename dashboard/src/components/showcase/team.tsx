const members = [
  { name: "Ava Chen", role: "Founder", initials: "AC" },
  { name: "Marcus Velez", role: "Engineering", initials: "MV" },
  { name: "Priya Shah", role: "Design", initials: "PS" },
  { name: "Noah Patel", role: "Growth", initials: "NP" },
];

export function Team() {
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-5xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Team</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Small, shipping, opinionated</h2>
        </div>
        <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
          {members.map((m) => (
            <div key={m.name} className="flex flex-col items-center text-center">
              <div className="flex h-24 w-24 items-center justify-center rounded-full bg-gradient-to-br from-primary/20 to-primary/40 text-xl font-semibold text-foreground ring-1 ring-border">
                {m.initials}
              </div>
              <p className="mt-4 font-medium">{m.name}</p>
              <p className="text-sm text-muted-foreground">{m.role}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
