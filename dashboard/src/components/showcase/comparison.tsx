import { Check, X } from "lucide-react";

const rows = [
  { feature: "Unlimited drafts", hobby: false, pro: true, studio: true },
  { feature: "All platforms", hobby: false, pro: true, studio: true },
  { feature: "A/B style rotation", hobby: false, pro: true, studio: true },
  { feature: "SSO", hobby: false, pro: false, studio: true },
  { feature: "Custom engagement styles", hobby: false, pro: false, studio: true },
  { feature: "Audit log", hobby: false, pro: false, studio: true },
  { feature: "Community support", hobby: true, pro: true, studio: true },
];

const Cell = ({ on }: { on: boolean }) =>
  on ? <Check className="mx-auto h-4 w-4 text-primary" /> : <X className="mx-auto h-4 w-4 text-muted-foreground/40" />;

export function Comparison() {
  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-4xl">
        <div className="mb-12 text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Compare plans</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">What's in each tier</h2>
        </div>
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-muted-foreground">
              <tr>
                <th className="px-6 py-4 text-left font-medium">Feature</th>
                <th className="px-6 py-4 text-center font-medium">Hobby</th>
                <th className="px-6 py-4 text-center font-medium text-primary">Pro</th>
                <th className="px-6 py-4 text-center font-medium">Studio</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((r) => (
                <tr key={r.feature}>
                  <td className="px-6 py-4 font-medium">{r.feature}</td>
                  <td className="px-6 py-4"><Cell on={r.hobby} /></td>
                  <td className="px-6 py-4 bg-primary/5"><Cell on={r.pro} /></td>
                  <td className="px-6 py-4"><Cell on={r.studio} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
