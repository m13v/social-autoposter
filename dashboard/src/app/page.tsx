import { getDb, Draft } from "@/lib/db";
import { DraftFeed } from "@/components/draft-feed";

export const dynamic = "force-dynamic";

const PLATFORMS = ["all", "reddit", "twitter", "linkedin", "moltbook", "email"];
const STATUSES = ["pending", "approved", "sent", "rejected", "edited"];

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ platform?: string; status?: string }>;
}) {
  const { platform, status } = await searchParams;
  const sql = getDb();

  const conditions: string[] = [];
  const params: string[] = [];

  if (platform && platform !== "all") {
    params.push(platform);
    conditions.push(`platform = $${params.length}`);
  }
  if (status) {
    params.push(status);
    conditions.push(`status = $${params.length}`);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
  const drafts = (await sql(
    `SELECT * FROM drafts ${where} ORDER BY created_at DESC LIMIT 100`,
    params
  )) as Draft[];

  const countRows = await sql(
    `SELECT status, COUNT(*)::int as count FROM drafts GROUP BY status`
  );
  const counts: Record<string, number> = {};
  for (const row of countRows) {
    counts[row.status as string] = row.count as number;
  }

  return (
    <main className="max-w-5xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-6">Draft Dashboard</h1>

      {/* Status filter pills */}
      <div className="flex gap-3 mb-6 flex-wrap">
        {STATUSES.map((s) => (
          <a
            key={s}
            href={`?status=${s}${platform ? `&platform=${platform}` : ""}`}
            className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
              status === s
                ? "bg-gray-900 text-white border-gray-900"
                : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
            }`}
          >
            {s} ({counts[s] || 0})
          </a>
        ))}
        <a
          href={platform ? `?platform=${platform}` : "/"}
          className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
            !status
              ? "bg-gray-900 text-white border-gray-900"
              : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
          }`}
        >
          all
        </a>
      </div>

      {/* Platform filter */}
      <div className="flex gap-2 mb-8 flex-wrap">
        {PLATFORMS.map((p) => (
          <a
            key={p}
            href={`?platform=${p}${status ? `&status=${status}` : ""}`}
            className={`px-3 py-1.5 rounded text-sm font-medium border transition-colors ${
              (platform || "all") === p
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-gray-700 border-gray-300 hover:border-blue-400"
            }`}
          >
            {p}
          </a>
        ))}
      </div>

      {drafts.length === 0 ? (
        <div className="text-center py-20 text-gray-400">
          <p className="text-lg">No drafts found</p>
          <p className="text-sm mt-2">
            Drafts will appear here when created via the API
          </p>
        </div>
      ) : (
        <DraftFeed initialDrafts={drafts} />
      )}
    </main>
  );
}
