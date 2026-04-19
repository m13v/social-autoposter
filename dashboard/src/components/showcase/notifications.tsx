import { Bell, Check, MessageCircle, UserPlus } from "lucide-react";

const items = [
  { icon: Check, title: "Post published", body: "Your LinkedIn draft went live.", time: "2m ago", unread: true },
  { icon: MessageCircle, title: "New comment", body: "@marcosv replied to your Reddit post.", time: "14m ago", unread: true },
  { icon: UserPlus, title: "New follower", body: "Priya Shah started following you.", time: "1h ago", unread: false },
  { icon: Bell, title: "Weekly digest", body: "3 posts, 41 replies, 2.1k impressions.", time: "Yesterday", unread: false },
];

export function Notifications() {
  return (
    <section className="bg-muted/30 px-6 py-24">
      <div className="mx-auto max-w-md">
        <div className="mb-8 flex items-center justify-between">
          <h2 className="text-2xl font-semibold">Notifications</h2>
          <button className="text-sm text-primary">Mark all read</button>
        </div>
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          {items.map((it, i) => (
            <div
              key={i}
              className={`flex items-start gap-3 border-b border-border px-5 py-4 last:border-b-0 ${it.unread ? "bg-primary/5" : ""}`}
            >
              <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-muted">
                <it.icon className="h-4 w-4" />
              </div>
              <div className="flex-1">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm font-medium">{it.title}</p>
                  {it.unread && <span className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-primary" />}
                </div>
                <p className="text-sm text-muted-foreground">{it.body}</p>
                <p className="mt-1 text-xs text-muted-foreground">{it.time}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
