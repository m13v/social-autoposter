import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const sql = getDb();
  const body = await req.json();
  const { action, edited_body, client_note } = body;
  const numId = parseInt(id, 10);

  if (action === "approve") {
    const rows = await sql`UPDATE drafts SET status = 'approved', reviewed_at = NOW() WHERE id = ${numId} RETURNING *`;
    return NextResponse.json(rows[0]);
  }

  if (action === "reject") {
    const rows = await sql`UPDATE drafts SET status = 'rejected', client_note = ${client_note || null}, reviewed_at = NOW() WHERE id = ${numId} RETURNING *`;
    return NextResponse.json(rows[0]);
  }

  if (action === "edit") {
    const rows = await sql`UPDATE drafts SET status = 'edited', edited_body = ${edited_body}, client_note = ${client_note || null}, reviewed_at = NOW() WHERE id = ${numId} RETURNING *`;
    return NextResponse.json(rows[0]);
  }

  if (action === "send") {
    const rows = await sql`UPDATE drafts SET status = 'sent', sent_at = NOW() WHERE id = ${numId} RETURNING *`;
    return NextResponse.json(rows[0]);
  }

  return NextResponse.json({ error: "Unknown action" }, { status: 400 });
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const sql = getDb();
  const numId = parseInt(id, 10);
  await sql`DELETE FROM drafts WHERE id = ${numId}`;
  return NextResponse.json({ ok: true });
}
