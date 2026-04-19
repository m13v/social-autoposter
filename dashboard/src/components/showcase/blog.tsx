import { ArrowRight } from "lucide-react";

const posts = [
  { tag: "Guide", title: "Why we rotate comment styles", author: "Matt", date: "Apr 12, 2026", read: "6 min" },
  { tag: "Engineering", title: "Schedulers that actually survive macOS sleep", author: "Matt", date: "Apr 3, 2026", read: "8 min" },
  { tag: "Post-mortem", title: "The LinkedIn ban that taught us Voyager is a trap", author: "Matt", date: "Apr 17, 2026", read: "4 min" },
];

export function Blog() {
  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-6xl">
        <div className="mb-12 flex items-end justify-between gap-4">
          <div>
            <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">From the blog</p>
            <h2 className="mt-2 text-4xl font-semibold tracking-tight">How the pipeline is built</h2>
          </div>
          <a href="#" className="hidden md:inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
            All posts <ArrowRight className="h-3.5 w-3.5" />
          </a>
        </div>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {posts.map((p) => (
            <a key={p.title} href="#" className="group overflow-hidden rounded-2xl border border-border bg-card transition-colors hover:bg-accent/40">
              <div className="aspect-[16/9] w-full bg-gradient-to-br from-primary/20 via-primary/10 to-background" />
              <div className="p-6">
                <span className="inline-flex rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium">{p.tag}</span>
                <h3 className="mt-4 text-lg font-semibold leading-snug group-hover:text-primary">{p.title}</h3>
                <p className="mt-4 text-sm text-muted-foreground">
                  {p.author} . {p.date} . {p.read}
                </p>
              </div>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
}
