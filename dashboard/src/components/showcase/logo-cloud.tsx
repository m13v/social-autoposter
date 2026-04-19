export function LogoCloud() {
  const logos = ["VERCEL", "NEXT.JS", "SUPABASE", "CLERK", "RESEND", "STRIPE", "LINEAR", "RAYCAST"];
  return (
    <section className="border-y border-border bg-muted/30 px-6 py-16">
      <div className="mx-auto max-w-5xl">
        <p className="text-center text-sm uppercase tracking-wider text-muted-foreground">
          Trusted by teams shipping every day
        </p>
        <div className="mt-10 grid grid-cols-2 items-center justify-items-center gap-8 sm:grid-cols-4 md:grid-cols-8">
          {logos.map((l) => (
            <span
              key={l}
              className="font-mono text-sm font-medium tracking-tight text-muted-foreground transition-colors hover:text-foreground"
            >
              {l}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}
