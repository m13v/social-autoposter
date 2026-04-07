import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";

export async function GET(req: NextRequest) {
  const sql = getDb();
  const { searchParams } = req.nextUrl;
  const status = searchParams.get("status");
  const platform = searchParams.get("platform");

  let rows;
  if (status && platform) {
    rows = await sql`SELECT * FROM drafts WHERE status = ${status} AND platform = ${platform} ORDER BY created_at DESC`;
  } else if (status) {
    rows = await sql`SELECT * FROM drafts WHERE status = ${status} ORDER BY created_at DESC`;
  } else if (platform) {
    rows = await sql`SELECT * FROM drafts WHERE platform = ${platform} ORDER BY created_at DESC`;
  } else {
    rows = await sql`SELECT * FROM drafts ORDER BY created_at DESC`;
  }

  return NextResponse.json(rows);
}

export async function POST(req: NextRequest) {
  const sql = getDb();
  const body = await req.json();

  const rows = await sql`
    INSERT INTO drafts (platform, content_type, title, body, target_url, target_title, target_author, target_snippet, our_account, project_name, metadata)
    VALUES (${body.platform}, ${body.content_type || "comment"}, ${body.title || null}, ${body.body}, ${body.target_url || null}, ${body.target_title || null}, ${body.target_author || null}, ${body.target_snippet || null}, ${body.our_account || null}, ${body.project_name || null}, ${JSON.stringify(body.metadata || {})})
    RETURNING *
  `;

  return NextResponse.json(rows[0], { status: 201 });
}
