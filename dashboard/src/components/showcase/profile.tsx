import { AtSign, MapPin } from "lucide-react";

export function ProfileCard() {
  return (
    <section className="px-6 py-24">
      <div className="mx-auto max-w-md">
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="h-24 bg-gradient-to-br from-primary/40 via-primary/20 to-background" />
          <div className="px-6 pb-6">
            <div className="-mt-10 flex items-end justify-between">
              <div className="flex h-20 w-20 items-center justify-center rounded-full border-4 border-card bg-gradient-to-br from-primary/30 to-primary/60 text-xl font-semibold">
                AC
              </div>
              <button className="mb-2 rounded-full border border-border bg-background px-4 py-1.5 text-sm font-medium">
                Follow
              </button>
            </div>
            <h3 className="mt-3 text-xl font-semibold">Ava Chen</h3>
            <p className="text-sm text-muted-foreground">Founder at Lumen Studio</p>
            <p className="mt-3 text-sm">
              Design systems, tiny teams, big shipping days. Writing on{" "}
              <span className="text-primary">@autoposter</span>.
            </p>
            <div className="mt-4 flex items-center gap-4 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <MapPin className="h-3 w-3" /> Brooklyn, NY
              </span>
              <span className="inline-flex items-center gap-1">
                <AtSign className="h-3 w-3" /> avacreates
              </span>
            </div>
            <div className="mt-4 flex gap-6 border-t border-border pt-4 text-sm">
              <div>
                <span className="font-semibold">12.4k</span>
                <span className="ml-1 text-muted-foreground">followers</span>
              </div>
              <div>
                <span className="font-semibold">483</span>
                <span className="ml-1 text-muted-foreground">following</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
