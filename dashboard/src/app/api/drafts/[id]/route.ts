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

  if (action === "approve") {
    const rows = await sql(
      `UPDATE drafts SET status = 'approved', reviewed_at = NOW() WHERE id = $1 RETURNING *`,
      [id]
    );
    return NextResponse.json(rows[0]);
  }

  if (action === "reject") {
    const rows = await sql(
      `UPDATE drafts SET status = 'rejected', client_note = $2, reviewed_at = NOW() WHERE id = $1 RETURNING *`,
      [id, client_note || null]
    );
    return NextResponse.json(rows[0]);
  }

  if (action === "edit") {
    const rows = await sql(
      `UPDATE drafts SET status = 'edited', edited_body = $2, client_note = $3, reviewed_at = NOW() WHERE id = $1 RETURNING *`,
      [id, edited_body, client_note || null]
    );
    return NextResponse.json(rows[0]);
  }

  if (action === "send") {
    const rows = await sql(
      `UPDATE drafts SET status = 'sent', sent_at = NOW() WHERE id = $1 RETURNING *`,
      [id]
    );
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
  await sql(`DELETE FROM drafts WHERE id = $1`, [id]);
  return NextResponse.json({ ok: true });
}
