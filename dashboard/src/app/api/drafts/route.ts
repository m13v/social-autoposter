import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";

export async function GET(req: NextRequest) {
  const sql = getDb();
  const { searchParams } = req.nextUrl;
  const status = searchParams.get("status");
  const platform = searchParams.get("platform");

  let query = `SELECT * FROM drafts`;
  const conditions: string[] = [];
  const params: string[] = [];

  if (status) {
    params.push(status);
    conditions.push(`status = $${params.length}`);
  }
  if (platform) {
    params.push(platform);
    conditions.push(`platform = $${params.length}`);
  }

  if (conditions.length > 0) {
    query += ` WHERE ${conditions.join(" AND ")}`;
  }
  query += ` ORDER BY created_at DESC`;

  const rows = await sql(query, params);
  return NextResponse.json(rows);
}

export async function POST(req: NextRequest) {
  const sql = getDb();
  const body = await req.json();

  const rows = await sql(
    `INSERT INTO drafts (platform, content_type, title, body, target_url, target_title, target_author, target_snippet, our_account, project_name, metadata)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
     RETURNING *`,
    [
      body.platform,
      body.content_type || "comment",
      body.title || null,
      body.body,
      body.target_url || null,
      body.target_title || null,
      body.target_author || null,
      body.target_snippet || null,
      body.our_account || null,
      body.project_name || null,
      JSON.stringify(body.metadata || {}),
    ]
  );

  return NextResponse.json(rows[0], { status: 201 });
}
