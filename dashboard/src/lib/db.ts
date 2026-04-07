import { neon } from "@neondatabase/serverless";

export function getDb() {
  return neon(process.env.DATABASE_URL!);
}

export type Draft = {
  id: number;
  platform: string;
  content_type: string;
  title: string | null;
  body: string;
  target_url: string | null;
  target_title: string | null;
  target_author: string | null;
  target_snippet: string | null;
  our_account: string | null;
  project_name: string | null;
  status: "pending" | "approved" | "sent" | "rejected" | "edited";
  client_note: string | null;
  edited_body: string | null;
  created_at: string;
  reviewed_at: string | null;
  sent_at: string | null;
  post_id: number | null;
  reply_id: number | null;
  metadata: Record<string, unknown>;
};
